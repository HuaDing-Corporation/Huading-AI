import { expect, test } from "@playwright/test";

/**
 * REVERSE-PROMPT-UI-0001 交互冒烟（承 ECOM-FIXES-0001「根本堵漏」思路）：生产构建(next start)下真走
 * 提示词反推全链——切 tab → 真上传图片(拿 source_asset_id) → 反推 → 结果块 + 近似重建红线 → 点「带入 ·
 * 数字人口播」→ **断言目标表单 topic 被预填**。这条端到端锁死「带入」链（page 缓冲 + 目标表单惰性消费），
 * 也堵运行时 #130（vitest 测不出的 prod tree-shaken undefined 组件白屏）。
 * 需以 NEXT_PUBLIC_USE_MOCK=1 构建后 next start 运行（webServer 已配）。
 */
test("提示词反推：上传→反推→带入·数字人口播预填 topic，无 #130 白屏", async ({ page }) => {
  const pageErrors: string[] = [];
  const react130: string[] = [];
  const doublePrefix: string[] = [];

  page.on("pageerror", (err) => pageErrors.push(String(err?.message ?? err)));
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Minified React error #130|error #130|client-side exception/.test(t)) react130.push(t);
  });
  page.on("request", (req) => {
    if (req.url().includes("/api/api")) doublePrefix.push(`${req.method()} ${req.url()}`);
  });

  await page.goto("/");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });

  if (await page.getByRole("button", { name: "登录" }).isVisible().catch(() => false)) {
    const inputs = page.locator("form input");
    await inputs.nth(0).fill("huading");
    await inputs.nth(1).fill("qa@huading.test");
    await inputs.nth(2).fill("pw123456");
    await page.getByRole("button", { name: "登录" }).click();
    await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
  }

  // 切到「提示词反推」模式。
  await page.getByRole("button", { name: "提示词反推" }).click();

  // 真上传图片（内存 buffer，mimeType 走白名单）→ 触发 /uploads/images 拿 asset_id。
  await page.locator('input[type="file"]').setInputFiles({
    name: "product.png",
    mimeType: "image/png",
    buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
  });

  // 上传完成后「开始反推」可点。
  const analyze = page.getByRole("button", { name: "开始反推" });
  await expect(analyze).toBeEnabled({ timeout: 15_000 });
  await analyze.click();

  // 结果块 + 近似重建红线（BE 下发 disclaimer）出现。
  await expect(page.getByText("不保证完全复刻原素材").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/浅景深特写/)).toBeVisible();

  // 带入 4 模块按钮齐备且可点。
  await expect(page.getByRole("button", { name: "带入 · 数字人口播" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "带入 · 电商带货" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "带入 · 视频生成" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "带入 · 电商图" })).toBeEnabled();

  // 点「带入 · 数字人口播」→ 切模式 + 目标表单预填 topic（mock fill_targets.avatar_talk.topic）。
  await page.getByRole("button", { name: "带入 · 数字人口播" }).click();
  await expect(page.locator("#video-topic")).toHaveValue("便携保温杯种草", { timeout: 15_000 });

  // 主门禁：无运行时 #130 白屏；无 /api/api 双前缀。
  expect(pageErrors, `page errors：\n${pageErrors.join("\n")}`).toEqual([]);
  expect(react130, `#130 console：\n${react130.join("\n")}`).toEqual([]);
  expect(doublePrefix, `/api/api 双前缀：\n${doublePrefix.join("\n")}`).toEqual([]);
});
