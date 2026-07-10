import { expect, test, type Page } from "@playwright/test";

/**
 * WORKBENCH-TAB-ORDER-0001 交互冒烟（生产构建 next start）：工作台顶部 7 个模式 tab 按用户 2026-07-10 指定新序渲染
 * ——数字人口播 · 提示词反推 · 图片生成/修改 · 电商图 · 文案仿写 · 电商带货 · 视频生成；默认仍数字人口播；各 tab 可点进；
 * 移动端（375）横向滚动不溢出。全程无 #130 白屏 / 无 /api/api 双前缀。需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
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

test("工作台 7 tab 新序 + 默认数字人口播 + 各 tab 可点进 + 移动端可见", async ({ page }) => {
  const g = await login(page);

  const group = page.getByRole("group", { name: "生成模式" });
  const tabs = group.getByRole("button");
  // 顺序锁：7 tab 按新序（toHaveText 数组同时校验数量 + 顺序）。
  await expect(tabs).toHaveText([/数字人口播/, /提示词反推/, /图片生成/, /电商图/, /文案仿写/, /电商带货/, /视频生成/]);

  // 默认仍数字人口播（第 1 位，aria-pressed=true）。
  await expect(page.getByRole("button", { name: /数字人口播/ })).toHaveAttribute("aria-pressed", "true");

  // 各 tab 可点进（点击 → aria-pressed 切换到该 tab）。
  for (const name of [/提示词反推/, /图片生成/, /电商图/, /文案仿写/, /电商带货/, /视频生成/]) {
    await group.getByRole("button", { name }).click();
    await expect(group.getByRole("button", { name })).toHaveAttribute("aria-pressed", "true");
  }
  // 切回默认不回归。
  await page.getByRole("button", { name: /数字人口播/ }).click();
  await expect(page.getByRole("button", { name: /数字人口播/ })).toHaveAttribute("aria-pressed", "true");

  // 移动端（375）：模式条横向滚动不溢出，首 tab 仍可见。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(group).toHaveClass(/overflow-x-auto/);
  await expect(page.getByRole("button", { name: /数字人口播/ })).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
