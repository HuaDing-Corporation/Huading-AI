import { expect, test, type Page } from "@playwright/test";

/**
 * ADMIN-VIP-GATE-UI-0001 交互冒烟（生产构建 next start，走 MSW）：
 *  ① 导航「数据看板」始终显示（去 adminOnly 隐藏）；② 授权（管理员/huading plan · mock 默认）点进 → 正常看板；
 *  ③ 未授权（localStorage hd_mock_analytics_plan=none → mock 403 ANALYTICS_PLAN_REQUIRED）→ 友好页
 *     「仅 huading plan 用户可查看」+ 返回工作台，不白屏、不透传 403、与「即将上线」页区分；④ 移动端 375。
 * 全程无 #130 / 无 /api/api 双前缀。需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
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
  if (await page.getByRole("button", { name: "登录" }).isVisible().catch(() => false)) {
    const inputs = page.locator("form input");
    await inputs.nth(0).fill("huading");
    await inputs.nth(1).fill("qa@huading.test");
    await inputs.nth(2).fill("pw123456");
    await page.getByRole("button", { name: "登录" }).click();
    await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
  }
}

test("数据看板导航始终显示 + 授权正常看板 + 未授权 VIP 友好页 + 移动端", async ({ page }) => {
  const g = watch(page);
  await login(page);

  // ① 导航「数据看板」始终显示（非隐藏）。
  await expect(page.getByRole("link", { name: "数据看板" })).toBeVisible();

  // ② 授权（mock 默认无 cookie=admin/huading）：点进 → 正常看板（区间选择器 + 概览渲染，无友好页）。
  await page.getByRole("link", { name: "数据看板" }).click();
  await page.waitForURL(/\/analytics$/, { timeout: 15_000 });
  await expect(page.getByRole("heading", { level: 1, name: "数据看板" })).toBeVisible();
  await expect(page.getByText("仅 huading plan 用户可查看")).toHaveCount(0);
  await expect(page.getByText("日期区间")).toBeVisible({ timeout: 15_000 }); // 区间选择器 = 看板已渲染

  // ③ 未授权：设 localStorage flag → mock 403 ANALYTICS_PLAN_REQUIRED → VIP 友好页（区别于「即将上线」）。
  await page.evaluate(() => window.localStorage.setItem("hd_mock_analytics_plan", "none"));
  await page.reload();
  await expect(page.getByText("仅 huading plan 用户可查看")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("link", { name: "返回工作台" })).toHaveAttribute("href", "/");
  // 不是「即将上线」占位页、不透传后端英文原串。
  await expect(page.getByText("该功能即将上线")).toHaveCount(0);
  await expect(page.getByText(/huading plan\.$/)).toHaveCount(0);
  // 页面标题仍在（不白屏）。
  await expect(page.getByRole("heading", { level: 1, name: "数据看板" })).toBeVisible();

  // ④ 移动端 375：友好页仍可见。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByText("仅 huading plan 用户可查看")).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
