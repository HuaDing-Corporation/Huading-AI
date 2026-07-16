import { expect, test, type Page } from "@playwright/test";

/**
 * WORKBENCH-KEEPALIVE-UI-0001 交互冒烟（生产构建 next start，真走 MSW）：工作台切模块不再丢当前模块的输入。
 * 三件事（jsdom 测不了、必须真实浏览器）：
 * 1) 承重：填输入 → 切走 → 切回 → 原样还在（改造前这里必空）。
 * 2) 首屏无回归：惰性挂载 —— 首屏只挂默认 mode，没把 7 个表单的 mount 请求全打出。
 * 3) a11y：隐藏面板真从可及性树消失 —— 不可见 / 不可聚焦 / Tab 进不去 / AX 树里读不到。
 * 需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
 */
const MODE_AVATAR = "数字人口播";
const MODE_PHOTO = /图片生成/;
const MODE_ECOM_IMAGE = "电商图";

async function login(page: Page) {
  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
  const inputs = page.locator("form input");
  await inputs.nth(0).fill("huading");
  await inputs.nth(1).fill("qa@huading.test");
  await inputs.nth(2).fill("pw123456");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
}

test("承重：切模块来回切 → 各模块的输入都原样保留", async ({ page }) => {
  await login(page);

  // 口播填主题（改造前：切走即卸载 → 切回全空）。
  await page.locator("#video-topic").fill("保温杯种草");

  // 切到图片生成，填它自己的提示词。
  await page.getByRole("button", { name: MODE_PHOTO }).click();
  await expect(page.getByTestId("panel-photo")).toBeVisible({ timeout: 15_000 });
  await page.locator("#photo-prompt").fill("白色大理石台面上的香水瓶");

  // 切回口播 → 主题还在。
  await page.getByRole("button", { name: MODE_AVATAR }).click();
  await expect(page.locator("#video-topic")).toHaveValue("保温杯种草");

  // 再切回图片生成 → 它的提示词也还在（两个模块各自的 state 互不串台）。
  await page.getByRole("button", { name: MODE_PHOTO }).click();
  await expect(page.locator("#photo-prompt")).toHaveValue("白色大理石台面上的香水瓶");
});

test("首屏无回归：惰性挂载——只挂默认 mode；来过的模块转为常驻隐藏", async ({ page }) => {
  const apiReqs: string[] = [];
  page.on("request", (r) => {
    const u = r.url();
    if (u.includes("/api/v1/")) apiReqs.push(`${r.method()} ${new URL(u).pathname}`);
  });

  await login(page);
  await expect(page.getByTestId("panel-avatar_talk")).toBeVisible({ timeout: 15_000 });

  // 惰性：其余 6 个 mode 的面板压根不在 DOM → 它们 mount 时的 GET 一个都没打。
  for (const m of ["photo", "ecom_image", "copywriting", "seedance_i2v", "video_gen", "reverse_prompt"]) {
    await expect(page.getByTestId(`panel-${m}`)).toHaveCount(0);
  }
  // 首屏请求里不含任何「非默认表单才会打」的端点（若 7 表单全挂即会出现）。
  expect(apiReqs.filter((r) => /ecom-images|\/bgm/.test(r)), `首屏请求：\n${apiReqs.join("\n")}`).toEqual([]);

  // 首次访问电商图 → 此时才挂载（按需拉取，不是「永不挂」）。
  await page.getByRole("button", { name: MODE_ECOM_IMAGE }).click();
  await expect(page.getByTestId("panel-ecom_image")).toBeVisible();

  // 切回口播 → 电商图面板仍在 DOM（常驻保活）但已隐藏。
  await page.getByRole("button", { name: MODE_AVATAR }).click();
  await expect(page.getByTestId("panel-ecom_image")).toHaveCount(1);
  await expect(page.getByTestId("panel-ecom_image")).toBeHidden();
});

test("a11y：隐藏面板从可及性树消失——不可见 / 不可聚焦 / Tab 进不去 / AX 树读不到", async ({ page }) => {
  await login(page);
  await page.locator("#video-topic").fill("保温杯种草");
  await page.getByRole("button", { name: MODE_PHOTO }).click();
  await expect(page.getByTestId("panel-photo")).toBeVisible({ timeout: 15_000 });

  const hiddenPanel = page.getByTestId("panel-avatar_talk");
  // 用 HTML hidden 属性隐藏（UA 样式 [hidden]{display:none}），而非 aria-hidden / visibility。
  await expect(hiddenPanel).toHaveAttribute("hidden", "");
  await expect(hiddenPanel).toBeHidden();
  await expect(page.locator("#video-topic")).toBeHidden();
  // 输入值仍在（隐藏 ≠ 卸载）——这正是本包的目的。
  await expect(page.locator("#video-topic")).toHaveValue("保温杯种草");

  // 硬证 1：隐藏面板里的输入框无法获得焦点（display:none 的元素不可聚焦）。
  const couldFocus = await page.evaluate(() => {
    const input = document.querySelector<HTMLInputElement>("#video-topic");
    input?.focus();
    return document.activeElement === input;
  });
  expect(couldFocus).toBe(false);

  // 硬证 2：连按 Tab 遍历，焦点绝不落进隐藏面板（不会形成「看不见的焦点黑洞」）。
  await page.keyboard.press("Tab");
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press("Tab");
    const inHidden = await page.evaluate(() => {
      const panel = document.querySelector('[data-testid="panel-avatar_talk"]');
      return !!panel && !!document.activeElement && panel.contains(document.activeElement);
    });
    expect(inHidden, `第 ${i + 1} 次 Tab 后焦点落进了隐藏面板`).toBe(false);
  }

  // 硬证 3：可及性树里读不到隐藏表单的内容（读屏不会读到 7 份表单）。
  // 走 Chrome DevTools Protocol 取完整 AX 树（page.accessibility 已从 Playwright 移除）。
  const client = await page.context().newCDPSession(page);
  await client.send("Accessibility.enable");
  const { nodes } = (await client.send("Accessibility.getFullAXTree")) as unknown as {
    nodes: Array<{ name?: { value?: string } }>;
  };
  const axNames = nodes.map((n) => n.name?.value ?? "").join(" | ");
  expect(axNames).not.toContain("新建视频"); // 隐藏的口播表单卡片标题
  expect(axNames).not.toContain("视频主题"); // 隐藏的口播表单字段 label
  expect(axNames).toContain("一句话生成图片"); // 当前可见的图片表单仍在树里（证明不是整棵树都空）
});
