import { expect, test, type Page } from "@playwright/test";

/**
 * HISTORY-UI-0001 图片历史统一模块 交互冒烟（生产构建 next start，真走 MSW list/detail 两端点 + 4 category）：
 * 进历史（左导航「图片历史」）→ 4 tab 可见 → 切「电商·详情图」→ 点开一套（重开整套弹窗）→ 下载原图（<a download>
 * 用 download_url，缺失禁用不死链）→ 移动端（375）tab 仍可见。全程无 #130 白屏 / 无 /api/api 双前缀。
 * 需以 NEXT_PUBLIC_USE_MOCK=1 构建后 next start 运行（webServer 已配）。
 */
async function login(page: Page): Promise<{ errors: () => string[]; doublePrefix: () => string[] }> {
  const errors: string[] = [];
  const doublePrefix: string[] = [];
  page.on("pageerror", (err) => errors.push(String(err?.message ?? err)));
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Minified React error #130|error #130|client-side exception/.test(t)) errors.push(t);
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
  return { errors: () => errors, doublePrefix: () => doublePrefix };
}

test("进历史→切详情图 tab→重开整套→下载原图；4 tab 齐备；移动端 375 可见", async ({ page }) => {
  const g = await login(page);

  // 左导航进入「图片历史」独立页。
  await page.getByRole("link", { name: "图片历史" }).click();
  await page.waitForURL("http://localhost:3100/history", { timeout: 15_000 });

  // 4 tab 齐备。
  for (const name of ["图片生成/修改", "电商·白底图", "电商·模特图", "电商·详情图"]) {
    await expect(page.getByRole("tab", { name })).toBeVisible();
  }

  // 切「电商·详情图」→ 两套历史项（主图 5 张 / 详情 12 张）。
  await page.getByRole("tab", { name: "电商·详情图" }).click();
  await expect(page.getByText("保温杯 · 主图复刻（5 张）")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("保温杯 · 详情页（12 张）")).toBeVisible();

  // 重开整套：点主图套卡片 → 弹窗展示 5 张，每张可下载原图（<a download> href=download_url）。
  await page.getByRole("button", { name: /保温杯 · 主图复刻/ }).click();
  const links = page.getByRole("link", { name: "下载原图" });
  await expect(links).toHaveCount(5, { timeout: 15_000 });
  await expect(links.first()).toHaveAttribute("href", /\?dl=1$/);
  await expect(links.first()).toHaveAttribute("download", /history-1\.png/);

  // 关闭弹窗（Esc），移动端（375）：4 tab 仍可见不溢出。
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 375, height: 812 });
  for (const name of ["图片生成/修改", "电商·详情图"]) {
    await expect(page.getByRole("tab", { name })).toBeVisible();
  }

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});

test("详情套（partial_failed，12 张含缺图）→ 缺图张禁用「原图暂不可用」不死链", async ({ page }) => {
  const g = await login(page);
  await page.getByRole("link", { name: "图片历史" }).click();
  await page.waitForURL("http://localhost:3100/history", { timeout: 15_000 });
  await page.getByRole("tab", { name: "电商·详情图" }).click();

  await page.getByRole("button", { name: /保温杯 · 详情页/ }).click();
  // 12 张里 11 张可下载 + 1 张缺图禁用态。
  await expect(page.getByRole("link", { name: "下载原图" })).toHaveCount(11, { timeout: 15_000 });
  await expect(page.getByText("原图暂不可用")).toBeVisible();
  // 部分失败提示如实展示。
  await expect(page.getByText("本套部分图片生成失败（缺失项不可下载）")).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
