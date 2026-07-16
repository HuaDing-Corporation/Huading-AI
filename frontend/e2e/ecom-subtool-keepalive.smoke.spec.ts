import { expect, test, type Page } from "@playwright/test";

/**
 * ECOM-SUBTOOL-KEEPALIVE-UI-0001 交互冒烟（生产构建 next start，真走 MSW）：电商图切**子工具**不再丢输入。
 * 顶层 mode 已由 WORKBENCH-KEEPALIVE-UI-0001 治好，这是同一个病的更小范围（体感与切模块一样）。
 * 三件事（jsdom 测不了、必须真实浏览器）：
 * 1) 承重：白底图/AI 模特各填输入 → 来回切子工具 → 都原样保留。
 * 2) 切换无回归：惰性挂载 —— AI 模特的 GET /ecom-images/model-styles 只在**首次访问该子工具**时才拉，
 *    进电商图不拉、切走再回来也不重拉。
 * 3) a11y：隐藏子工具真从可及性树消失 —— 不可见 / 不可聚焦 / Tab 进不去 / AX 树读不到。
 * 需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
 */
const MODE_ECOM_IMAGE = "电商图";
const SUB_CUTOUT = "白底图";
const SUB_MODEL = "AI 模特";
const SUB_DETAIL = "电商详情图";

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
const gotoSubtool = (page: Page, name: string) => page.getByRole("button", { name, exact: true }).click();

test("承重：电商图来回切子工具 → 各子工具的输入都原样保留", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: MODE_ECOM_IMAGE, exact: true }).click();

  // 白底图（默认子工具）：背景选「透明底」。
  const cutout = page.getByTestId("subtool-cutout");
  await expect(cutout).toBeVisible({ timeout: 15_000 });
  await cutout.getByRole("button", { name: "透明底" }).click();
  await expect(cutout.getByRole("button", { name: "透明底" })).toHaveAttribute("aria-pressed", "true");

  // 切 AI 模特：填自定义补充。
  await gotoSubtool(page, SUB_MODEL);
  const model = page.getByTestId("subtool-model");
  await expect(model).toBeVisible();
  await model.locator("#ecom-model-custom").fill("工作室柔光、简洁白底");

  // 切回白底图：透明底仍选中（改造前：卸载 → 回落默认「白底」）。
  await gotoSubtool(page, SUB_CUTOUT);
  await expect(cutout.getByRole("button", { name: "透明底" })).toHaveAttribute("aria-pressed", "true");

  // 再切 AI 模特：自定义补充还在（改造前：卸载 → 清空）。
  await gotoSubtool(page, SUB_MODEL);
  await expect(model.locator("#ecom-model-custom")).toHaveValue("工作室柔光、简洁白底");
});

test("切换无回归：惰性挂载——model-styles 只在首次访问 AI 模特时拉一次", async ({ page }) => {
  const apiReqs: string[] = [];
  page.on("request", (r) => {
    const u = r.url();
    if (u.includes("/api/v1/")) apiReqs.push(new URL(u).pathname);
  });
  const styleReqs = () => apiReqs.filter((p) => p.includes("/ecom-images/model-styles"));

  await login(page);
  await page.getByRole("button", { name: MODE_ECOM_IMAGE, exact: true }).click();
  await expect(page.getByTestId("subtool-cutout")).toBeVisible({ timeout: 15_000 });

  // 惰性：进电商图只挂白底图；另两个子工具压根不在 DOM → AI 模特的 mount 请求一个都没打。
  await expect(page.getByTestId("subtool-model")).toHaveCount(0);
  await expect(page.getByTestId("subtool-detail")).toHaveCount(0);
  expect(styleReqs(), `请求：\n${apiReqs.join("\n")}`).toEqual([]);

  // 首次访问 AI 模特 → 此时才按需拉取（证明是「惰性」而非「永不挂」）。
  await gotoSubtool(page, SUB_MODEL);
  await expect(page.getByTestId("subtool-model")).toBeVisible();
  await expect.poll(() => styleReqs().length, { timeout: 10_000 }).toBe(1);

  // 切走再回来：子工具常驻不重挂 → 不重复拉取。
  await gotoSubtool(page, SUB_CUTOUT);
  await gotoSubtool(page, SUB_MODEL);
  await expect(page.getByTestId("subtool-model")).toBeVisible();
  expect(styleReqs()).toHaveLength(1);

  // 常驻：三个来过的子工具都还在 DOM，只有当前那个可见。
  await gotoSubtool(page, SUB_DETAIL);
  await expect(page.getByTestId("subtool-detail")).toBeVisible();
  await expect(page.getByTestId("subtool-cutout")).toBeHidden();
  await expect(page.getByTestId("subtool-model")).toBeHidden();
  await expect(page.getByTestId("subtool-cutout")).toHaveCount(1);
});

test("a11y：隐藏子工具从可及性树消失——不可见 / 不可聚焦 / Tab 进不去 / AX 树读不到", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: MODE_ECOM_IMAGE, exact: true }).click();
  await expect(page.getByTestId("subtool-cutout")).toBeVisible({ timeout: 15_000 });

  // 切到 AI 模特 → 白底图转隐藏（但输入仍在，见承重用例）。
  await gotoSubtool(page, SUB_MODEL);
  const hidden = page.getByTestId("subtool-cutout");
  await expect(hidden).toHaveAttribute("hidden", "");
  await expect(hidden).toBeHidden();
  await expect(page.getByTestId("subtool-model")).toHaveClass("contents");

  // 硬证 1：隐藏子工具里的控件无法获得焦点（display:none 元素不可聚焦）。
  const couldFocus = await page.evaluate(() => {
    const el = document.querySelector<HTMLElement>('[data-testid="subtool-cutout"] button');
    el?.focus();
    return !!el && document.activeElement === el;
  });
  expect(couldFocus).toBe(false);

  // 硬证 2：连按 Tab 遍历，焦点绝不落进隐藏子工具。
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press("Tab");
    const inHidden = await page.evaluate(() => {
      const panel = document.querySelector('[data-testid="subtool-cutout"]');
      return !!panel && !!document.activeElement && panel.contains(document.activeElement);
    });
    expect(inHidden, `第 ${i + 1} 次 Tab 后焦点落进了隐藏子工具`).toBe(false);
  }

  // 硬证 3：可及性树里读不到隐藏子工具的内容（读屏不会读到 3 份表单）。走 CDP 取完整 AX 树。
  const client = await page.context().newCDPSession(page);
  await client.send("Accessibility.enable");
  const { nodes } = (await client.send("Accessibility.getFullAXTree")) as unknown as {
    nodes: Array<{ name?: { value?: string } }>;
  };
  const axNames = nodes.map((n) => n.name?.value ?? "").join(" | ");
  expect(axNames).not.toContain("透明底"); // 白底图独有的背景选项（隐藏中）
  expect(axNames).not.toContain("背景"); // 白底图独有的字段 label
  expect(axNames).toContain(SUB_MODEL); // 当前可见的 AI 模特子工具仍在树里
});
