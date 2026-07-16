import { expect, test, type Page } from "@playwright/test";

/**
 * REVERSE-PROMPT-UI 交互冒烟（承 ECOM-FIXES-0001「根本堵漏」；FIX1 对齐 BE 真契约后加电商图档断言）：
 * 生产构建(next start)下真走全链——切 tab → 真上传图片(拿 source_asset_id) → 反推(请求体仅 source_asset_id)
 * → 结果块 + 近似重建红线 → 点「带入」→ **断言目标表单被预填**。两条端到端锁死带入落点（page 缓冲 + 目标
 * 表单惰性消费），也堵运行时 #130（vitest 测不出的 prod tree-shaken undefined 组件白屏）。
 * 需以 NEXT_PUBLIC_USE_MOCK=1 构建后 next start 运行（webServer 已配）。
 */

async function gotoReverseResult(page: Page): Promise<{ errors: () => string[]; doublePrefix: () => string[] }> {
  const pageErrors: string[] = [];
  const doublePrefix: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err?.message ?? err)));
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Minified React error #130|error #130|client-side exception/.test(t)) pageErrors.push(t);
  });
  page.on("request", (req) => {
    if (req.url().includes("/api/api")) doublePrefix.push(`${req.method()} ${req.url()}`);
  });

  // LANDING-ENTRY-UI-0001：未登录进站根已改落 /landing → helper 直达 /login（登录页行为不变）。
  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });

  if (await page.getByRole("button", { name: "登录" }).isVisible().catch(() => false)) {
    const inputs = page.locator("form input");
    await inputs.nth(0).fill("huading");
    await inputs.nth(1).fill("qa@huading.test");
    await inputs.nth(2).fill("pw123456");
    await page.getByRole("button", { name: "登录" }).click();
    await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
  }

  await page.getByRole("button", { name: "提示词反推" }).click();
  // WORKBENCH-KEEPALIVE-UI-0001：面板改为常驻后，来过的其它表单（如口播的 #avatar-image）仍留在 DOM，
  // 全局 input[type=file] 会命中多个 → 选择器必须限定到当前面板。
  await page.getByTestId("panel-reverse_prompt").locator('input[type="file"]').setInputFiles({
    name: "product.png",
    mimeType: "image/png",
    buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
  });
  const analyze = page.getByRole("button", { name: "开始反推" });
  await expect(analyze).toBeEnabled({ timeout: 15_000 });
  await analyze.click();
  // 结果块 + 近似重建红线（BE 下发 disclaimer）。
  await expect(page.getByText("不保证完全复刻原素材").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/浅景深特写/)).toBeVisible();

  return { errors: () => pageErrors, doublePrefix: () => doublePrefix };
}

test("带入·数字人口播 → 预填 topic + script，无 #130 白屏", async ({ page }) => {
  const g = await gotoReverseResult(page);

  // 5 键「带入」齐备且可点（营销海报已下线 ECOM-REPLICATE-UI-0001，无该按钮）。
  for (const name of ["带入 · 数字人口播", "带入 · 电商带货", "带入 · 视频生成", "带入 · 图片生成", "带入 · AI 模特"]) {
    await expect(page.getByRole("button", { name })).toBeEnabled();
  }
  // 海报入口彻底移除 → 无「带入 · 营销海报」按钮
  await expect(page.getByRole("button", { name: "带入 · 营销海报" })).toHaveCount(0);

  await page.getByRole("button", { name: "带入 · 数字人口播" }).click();
  // avatar_talk 落点：topic→#video-topic、script→#video-script（mock fill_targets.avatar_talk）。
  await expect(page.locator("#video-topic")).toHaveValue("便携保温杯种草", { timeout: 15_000 });
  await expect(page.locator("#video-script")).toHaveValue(/大家好，今天给大家安利这款便携保温杯/);

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});

test("带入·AI 模特 → 切电商图·AI 模特子工具并预填自定义补充（电商图档落点）", async ({ page }) => {
  const g = await gotoReverseResult(page);

  // ecom_model 落点：切到电商图 mode + AI 模特子工具，extra_prompt→#ecom-model-custom（mock ecom_model.extra_prompt）。
  await page.getByRole("button", { name: "带入 · AI 模特" }).click();
  // ECOM-SUBTOOL-KEEPALIVE-UI-0001：子工具改为常驻后，#ecom-model-custom 在隐藏态也留在 DOM，而 toHaveValue
  // **不校验可见性** → 单靠它已不能证明「确实切到了 AI 模特子工具」。补一条可见性断言把落点锁死。
  const custom = page.getByTestId("panel-ecom_image").locator("#ecom-model-custom");
  await expect(custom).toBeVisible({ timeout: 15_000 });
  await expect(custom).toHaveValue("工作室柔光、简洁白底、突出质感");

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
