import { expect, test, type Page } from "@playwright/test";

/**
 * ECOM-REPLICATE-UI-0001 电商详情图·复刻向导 交互冒烟（生产构建 next start，真走 MSW 两阶段状态机）：
 * ① 主图流：切电商图→电商详情图→模式「主图(5张)」→真上传参考图+商品图→商品信息+卖点→生成规划表(整套总价取后端)
 *    →确认扣费→轮询生成(禁分批)→结果一次性 5 张→**下载给原图 URL(<a download>) + 显示 AI 原始尺寸**（原图红线）。
 * ② 详情流：模式「详情页(12张)」→整套 12 张 + 后端总价 1560；移动端(375)三子工具可见。
 * 全程无 #130 白屏 / 无 /api/api 双前缀。需以 NEXT_PUBLIC_USE_MOCK=1 构建后 next start 运行（webServer 已配）。
 */
const PNG = { name: "ref.png", mimeType: "image/png", buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]) };

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

/** 进入电商详情图向导并完成上传 + 文案（返回时停在上传步，可点「生成规划表」）。 */
async function fillUpload(page: Page, mode: "主图（5 张）" | "详情页（12 张）"): Promise<void> {
  await page.getByRole("button", { name: "电商图", exact: true }).click();
  await page.getByRole("button", { name: "电商详情图", exact: true }).click();
  await page.getByRole("button", { name: mode, exact: true }).click();

  await page.locator('input[type="file"]#ecom-detail-ref').setInputFiles(PNG);
  await page.locator('input[type="file"]#ecom-detail-product').setInputFiles(PNG);
  // 上传后按钮显示 1/{ref上限}（ECOM-REF-LIMIT-UI-0001：参考图上限随模式 主图 5 / 详情 12；商品图恒 4）。
  const refMax = mode === "主图（5 张）" ? 5 : 12;
  await expect(page.getByRole("button", { name: new RegExp(`上传参考图.*1\\/${refMax}`) })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: /上传商品图.*1\/4/ })).toBeVisible({ timeout: 15_000 });

  await page.locator("#ecom-detail-info").fill("316 不锈钢保温杯，600ml");
  await page.getByPlaceholder("一条卖点，如：316 不锈钢，24 小时持续锁温").fill("24 小时持续锁温");
}

test("主图流：上传→规划(75积分)→确认扣费→一次性 5 张→下载原图(<a download>)+原始尺寸(原图红线)", async ({ page }) => {
  const g = await login(page);
  await fillUpload(page, "主图（5 张）");

  await page.getByRole("button", { name: "生成规划表" }).click();
  // 规划表 + 整套总价取后端 total_credits（主图 5×130=650）+ 未裁剪红线列。
  await expect(page.getByText("生成规划（确认后按此复刻，仅确认一次）")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("整套预计 650 积分")).toBeVisible();
  await expect(page.getByText("原图不裁剪").first()).toBeVisible();

  // 扣费门：确认恰一次。
  await page.getByRole("button", { name: "确认并生成" }).click();
  await page.getByRole("button", { name: "确认扣费生成" }).click();

  // 生成中禁分批（此刻无下载入口）→ 轮询完成后一次性 5 张。
  await expect(page.getByText("复刻结果（整套）")).toBeVisible({ timeout: 25_000 });
  const links = page.getByRole("link", { name: "下载原图" });
  await expect(links).toHaveCount(5);
  // 原图红线：下载给原图 bytes（href 指原图 URL、有 download 属性），零前端后处理。
  await expect(links.first()).toHaveAttribute("href", /\?dl=1$/);
  await expect(links.first()).toHaveAttribute("download", /ecom-replicate-1\.png/);
  // 不隐藏 AI 原始尺寸（1024x1024 → APIMart 实返 1254x1254）。
  await expect(page.getByText(/1254x1254/).first()).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});

test("详情流：模式「详情页(12张)」→规划 1560 积分 + 12 张；移动端(375)三子工具可见", async ({ page }) => {
  const g = await login(page);
  await fillUpload(page, "详情页（12 张）");

  await page.getByRole("button", { name: "生成规划表" }).click();
  await expect(page.getByText("生成规划（确认后按此复刻，仅确认一次）")).toBeVisible({ timeout: 15_000 });
  // 详情页 12×130=1560，尺寸 768x1024（12 行）。
  await expect(page.getByText("整套预计 1560 积分")).toBeVisible();
  await expect(page.getByText("768x1024").first()).toBeVisible();

  await page.getByRole("button", { name: "确认并生成" }).click();
  await page.getByRole("button", { name: "确认扣费生成" }).click();
  await expect(page.getByText("复刻结果（整套）")).toBeVisible({ timeout: 25_000 });
  await expect(page.getByRole("link", { name: "下载原图" })).toHaveCount(12);

  // 移动端（375）：三子工具（白底图 / AI 模特 / 电商详情图）仍可见不溢出。
  await page.setViewportSize({ width: 375, height: 812 });
  for (const name of ["白底图", "AI 模特", "电商详情图"]) {
    await expect(page.getByRole("button", { name, exact: true })).toBeVisible();
  }

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});

// ECOM-REF-LIMIT-UI-0001：参考图上限随模式 + 🔴 修静默截断——详情传 6 张 → 切主图(上限5) → 明确越限提示、阻止提交、绝不丢图。
const REFS6 = Array.from({ length: 6 }, (_, i) => ({ name: `r${i}.png`, mimeType: "image/png", buffer: PNG.buffer }));
test("参考图上限随模式 + 修静默截断：详情传 6 张 → 切主图 → 越限提示阻止提交（不丢图）；移动端", async ({ page }) => {
  const g = await login(page);
  await page.getByRole("button", { name: "电商图", exact: true }).click();
  await page.getByRole("button", { name: "电商详情图", exact: true }).click();

  // 详情模式（上限 12）：传 6 张参考图 + 1 商品图。
  await page.getByRole("button", { name: "详情页（12 张）", exact: true }).click();
  await page.locator('input[type="file"]#ecom-detail-ref').setInputFiles(REFS6);
  await page.locator('input[type="file"]#ecom-detail-product').setInputFiles(PNG);
  await expect(page.getByRole("button", { name: /上传参考图.*6\/12/ })).toBeVisible({ timeout: 15_000 });
  await page.locator("#ecom-detail-info").fill("316 不锈钢保温杯");
  await page.getByPlaceholder("一条卖点，如：316 不锈钢，24 小时持续锁温").fill("24 小时锁温");

  // 切主图（上限 5，已传 6）→ 生成规划表 → 明确越限提示、不进规划表、不丢图（6 张仍在）。
  await page.getByRole("button", { name: "主图（5 张）", exact: true }).click();
  await page.getByRole("button", { name: "生成规划表" }).click();
  await expect(page.getByText("参考图最多 5 张（当前模式），请删减到 5 张以内后再生成")).toBeVisible();
  await expect(page.getByText("生成规划（确认后按此复刻，仅确认一次）")).toHaveCount(0);
  // 不丢图：6 张仍在（切模式后计数不被静默裁剪；主图上限 5 但已传 6 待用户删减）。
  await expect(page.getByRole("button", { name: /上传参考图.*6\/5/ })).toBeVisible();

  // 移动端 375：越限提示仍可见。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByText("参考图最多 5 张（当前模式），请删减到 5 张以内后再生成")).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
