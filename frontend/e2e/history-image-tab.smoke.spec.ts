import { expect, test, type Page } from "@playwright/test";

/**
 * HISTORY-IMAGE-TAB-UI-0001 交互冒烟（生产构建 next start，真走 MSW /history/images list/detail + 6 category）：
 * 图片历史已并进工作台「历史生成」的图片 tab（/history 独立页下线）。验证：6 分类 chip → 切「电商·详情图」→
 * 「查看详情」重开整套(5 张 + 下载原图 + 信息并集：状态/分类/张数) → 点图开大图弹窗 → partial 缺图不死链。
 * HISTORY-CHAT-DELETE-UI-0001：删除入口复活（纯记录软删，不碰媒体）→ 本冒烟末尾追加「删一条 → 卡片真的消失、其余不少」。
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
  await expect(page.getByText("保温杯 · 详情页（11 张成功）")).toBeVisible();

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

test("详情套（partial_failed）→ 只返 11 张成功、全部可下载(非死链) + 部分失败提示", async ({ page }) => {
  const g = watch(page);
  await login(page);
  await page.getByRole("tab", { name: "图片历史" }).click();
  await page.getByRole("button", { name: "电商·详情图" }).click();

  await detailCard(page, /保温杯 · 详情页（11 张成功）/).getByRole("button", { name: "查看详情" }).click();
  // FIX2 对齐真实 BE：详情只返成功张（失败张已 omit，schema download_url:str 非空）→ 11 张全部可下载、无「缺图」死链；
  // 套级 status 仍标 partial_failed（从 task 带出）→ 展示「仅展示成功生成的图片」提示。
  const links = page.getByRole("link", { name: "下载原图" });
  await expect(links).toHaveCount(11, { timeout: 15_000 });
  await expect(links.first()).toHaveAttribute("href", /\?dl=1$/);
  await expect(page.getByText("本套部分图片生成失败，仅展示成功生成的图片")).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});

// HISTORY-CHAT-DELETE-UI-0001 · 真栈删除冒烟：卡片删除入口（**底部行、不与缩略图角标抢位**——#218 教训）
// → 二次确认 → 该卡真的从列表消失、其余一条不少。mock 是**真删**（从 historyImageRecords 移除），
// 所以"删除后列表刷新"这条在真栈里是可证的（不是返 200 而列表照旧）。
test("图片 tab：删一条 → 二次确认 → 卡片消失、其余不少（Console 0）", async ({ page }) => {
  const g = watch(page);
  await login(page);
  await page.getByRole("tab", { name: "图片历史" }).click();
  await page.getByRole("button", { name: "图片生成/修改" }).click();

  const cards = page.getByTestId("history-card");
  await expect(cards.first()).toBeVisible({ timeout: 15_000 });
  const firstTitle = ((await cards.first().locator("p").first().textContent()) ?? "").trim();
  expect(firstTitle).not.toBe("");
  // 该标题此刻确实在页面上（删除前的基线）。
  await expect(page.getByText(firstTitle, { exact: true })).toHaveCount(1);

  // 删除按钮在卡片底部行，与「查看详情」同排（不在缩略图内）。
  await cards.first().getByRole("button", { name: "删除" }).click();
  await expect(page.getByText("将从历史移除，无法撤销。")).toBeVisible();
  await page.getByRole("button", { name: "确认删除" }).click();

  // 🔴 断言「被删的那条真的消失」而不是「总数 -1」：本页 page_size=20 且该分类种子 >20，
  // 删一条后 infiniteQuery 重取会把下一条补进首页 → 总数仍是 20（分页语义的正常行为，非漏删）。
  // 「那条不见了」才是删除的可观察后果，也不受补位干扰。
  await expect(page.getByText(firstTitle, { exact: true })).toHaveCount(0, { timeout: 15_000 });
  // 且列表没被清空/塌陷（其余条目仍在）。
  expect(await cards.count()).toBeGreaterThan(1);

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
