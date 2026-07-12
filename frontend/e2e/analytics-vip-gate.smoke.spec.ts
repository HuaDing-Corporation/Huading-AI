import { expect, test, type Page } from "@playwright/test";

/**
 * PROD-P0-ANALYTICS-TENANT-LEAK-UI-0001 交互冒烟（生产构建 next start，走 MSW）——数据看板三态，门禁走 permissions：
 *  ① 平台方（默认平台租户 → analytics_platform）：全站视图，含「用户排行」，零回归；
 *  ② VIP 客户（普通租户 + huading → analytics_view 无 platform）：看板渲染但**隐藏「用户排行」**、副标题「我的用量」、
 *     只自己的数据（无别租户名）；③ 新注册普通用户（普通租户 + free → 无 analytics_view）：友好页「仅 huading plan 用户可查看」。
 * 各态含移动端 375。全程无 #130 / 无 /api/api 双前缀。需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
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

// 登录前置三旋钮 → mock login/me 按「平台租户/套餐」派生 permissions（platform 传 "0" = 普通租户，不传 = 默认平台方）。
async function loginAs(page: Page, opts: { platform?: "0" | "1"; plan?: "huading" | "free" }) {
  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
  await page.evaluate(
    ([platform, plan]) => {
      if (platform) window.localStorage.setItem("hd_mock_platform", platform);
      else window.localStorage.removeItem("hd_mock_platform");
      window.localStorage.setItem("hd_mock_plan", plan);
    },
    [opts.platform ?? "", opts.plan ?? "huading"]
  );
  const inputs = page.locator("form input");
  await inputs.nth(0).fill("huading");
  await inputs.nth(1).fill("qa@huading.test");
  await inputs.nth(2).fill("pw123456");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
}

test("① 平台方：导航始终显示 + 全站看板（含「用户排行」）零回归", async ({ page }) => {
  const g = watch(page);
  await loginAs(page, { platform: "1", plan: "huading" });

  await expect(page.getByRole("link", { name: "数据看板" })).toBeVisible();
  await page.getByRole("link", { name: "数据看板" }).click();
  await page.waitForURL(/\/analytics$/, { timeout: 15_000 });
  await expect(page.getByRole("heading", { level: 1, name: "数据看板" })).toBeVisible();
  await expect(page.getByText("仅 huading plan 用户可查看")).toHaveCount(0);
  await expect(page.getByText("日期区间")).toBeVisible({ timeout: 15_000 });
  // 平台方看全站：「用户排行」在 + 副标题「全站」。
  await expect(page.getByRole("heading", { name: "用户排行" })).toBeVisible();
  await expect(page.getByText("全站用量、成本与趋势总览")).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});

test("② VIP 客户（huading，非平台）：看板渲染但隐藏「用户排行」+ 副标题「我的用量」+ 无别租户名；移动端", async ({ page }) => {
  const g = watch(page);
  await loginAs(page, { platform: "0", plan: "huading" });

  await page.getByRole("link", { name: "数据看板" }).click();
  await page.waitForURL(/\/analytics$/, { timeout: 15_000 });
  // 能进（非友好页）+ 概览渲染。
  await expect(page.getByText("仅 huading plan 用户可查看")).toHaveCount(0);
  await expect(page.getByText("日期区间")).toBeVisible({ timeout: 15_000 });
  // 🔴 防泄漏：无「用户排行」、无其它租户名（ANALYTICS_TENANTS 的「用户 N」）、副标题为「我的用量」。
  await expect(page.getByRole("heading", { name: "用户排行" })).toHaveCount(0);
  await expect(page.getByText(/^用户 \d+$/)).toHaveCount(0);
  await expect(page.getByText("我的用量、成本与趋势总览")).toBeVisible();
  await expect(page.getByText("全站用量、成本与趋势总览")).toHaveCount(0);
  // 移动端 375：仍无「用户排行」。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByRole("heading", { name: "用户排行" })).toHaveCount(0);

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});

test("③ 新注册普通用户（free，非平台）：无 analytics_view → 友好页；移动端", async ({ page }) => {
  const g = watch(page);
  await loginAs(page, { platform: "0", plan: "free" });

  // 导航仍显示（不按 role 隐藏）；点进 → 友好页（BE 真 403 ANALYTICS_PLAN_REQUIRED）。
  await expect(page.getByRole("link", { name: "数据看板" })).toBeVisible();
  await page.getByRole("link", { name: "数据看板" }).click();
  await page.waitForURL(/\/analytics$/, { timeout: 15_000 });
  await expect(page.getByText("仅 huading plan 用户可查看")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("link", { name: "返回工作台" })).toHaveAttribute("href", "/");
  // 不白屏、不是「即将上线」、不透传英文原串、看不到任何租户名。
  await expect(page.getByRole("heading", { level: 1, name: "数据看板" })).toBeVisible();
  await expect(page.getByText("该功能即将上线")).toHaveCount(0);
  await expect(page.getByText(/huading plan\.$/)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "用户排行" })).toHaveCount(0);
  // 移动端 375：友好页仍在。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByText("仅 huading plan 用户可查看")).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
