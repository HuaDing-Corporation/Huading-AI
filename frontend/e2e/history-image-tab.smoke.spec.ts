import { expect, test, type Page } from "@playwright/test";

/**
 * HISTORY-IMAGE-TAB-UI-0001 交互冒烟（生产构建 next start，真走 MSW /history/images list/detail + 6 category）：
 * 图片历史已并进工作台「历史生成」的图片 tab（/history 独立页下线）。验证：6 分类 chip → 切「电商·详情图」→
 * 「查看详情」重开整套(5 张 + 下载原图 + 信息并集：状态/分类/张数) → 点图开大图弹窗 → partial 缺图不死链。
 * FIX1：归一 API 的图片删除端点被摘（用户「三拆」改 GC 方案）→ 图片 tab 暂无删除入口，本冒烟不再测删除。
 * 全程无 #130 白屏 / 无 /api/api 双前缀。需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
 */
function watch(page: Page): { errors: () => string[]; doublePrefix: () => string[] } {
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
  return { errors: () => errors, doublePrefix: () => doublePrefix };
}
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
const detailCard = (page: Page, title: RegExp) => page.getByTestId("history-card").filter({ hasText: title });

test("图片 tab：6 分类 + 查看详情(整套+信息并集) + 大图弹窗；375 可见", async ({ page }) => {
  const g = watch(page);
  await login(page);

  // 进工作台「历史生成」的图片 tab（下线 /history 后的新家）。
  await page.getByRole("tab", { name: "图片历史" }).click();
  // 6 分类 chip 齐备（顺序：全部图片 / 图片生成·修改 / 白底 / 模特 / 详情 / 封面）。
  for (const name of ["全部图片", "图片生成/修改", "电商·白底图", "电商·模特图", "电商·详情图", "封面"]) {
    await expect(page.getByRole("button", { name })).toBeVisible({ timeout: 15_000 });
  }

  // 切「电商·详情图」→ 两套历史项。
  await page.getByRole("button", { name: "电商·详情图" }).click();
  await expect(page.getByText("保温杯 · 主图复刻（5 张）")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("保温杯 · 详情页（12 张）")).toBeVisible();

  // 「查看详情」→ 重开整套弹窗：5 张 + 下载原图（?dl=1）+ 信息并集（分类/张数）。
  await detailCard(page, /保温杯 · 主图复刻（5 张）/).getByRole("button", { name: "查看详情" }).click();
  const links = page.getByRole("link", { name: "下载原图" });
  await expect(links).toHaveCount(5, { timeout: 15_000 });
  await expect(links.first()).toHaveAttribute("href", /\?dl=1$/);
  await expect(links.first()).toHaveAttribute("download", /history-1\.png/);
  // 信息并集（原 /history 弹窗没显、卡片有）：分类「电商·详情图」+ 张数「共 5 张」。
  await expect(page.getByText("共 5 张")).toBeVisible();
  await expect(page.getByText(/分类：电商·详情图/)).toBeVisible();
  await page.keyboard.press("Escape");

  // 点卡片图片 → 纯图片大图弹窗。
  await detailCard(page, /保温杯 · 主图复刻（5 张）/).getByRole("button", { name: "查看大图" }).click();
  await expect(page.getByRole("img", { name: /保温杯 · 主图复刻（5 张）（大图预览）/ })).toBeVisible({ timeout: 15_000 });
  await page.keyboard.press("Escape");

  // FIX1：图片删除端点被摘（用户「三拆」改 GC 方案）→ 图片卡片不再有删除入口，故本冒烟不测删除。
  // 移动端 375：分类 chip 仍可见（flex-wrap 不溢出）。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByRole("button", { name: "全部图片" })).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});

test("详情套（partial_failed，12 张含缺图）→ 缺图张禁用「原图暂不可用」不死链", async ({ page }) => {
  const g = watch(page);
  await login(page);
  await page.getByRole("tab", { name: "图片历史" }).click();
  await page.getByRole("button", { name: "电商·详情图" }).click();

  await detailCard(page, /保温杯 · 详情页（12 张）/).getByRole("button", { name: "查看详情" }).click();
  // 12 张里 11 张可下载 + 1 张缺图禁用态 + 部分失败提示。
  await expect(page.getByRole("link", { name: "下载原图" })).toHaveCount(11, { timeout: 15_000 });
  await expect(page.getByText("原图暂不可用")).toBeVisible();
  await expect(page.getByText("本套部分图片生成失败（缺失项不可下载）")).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
