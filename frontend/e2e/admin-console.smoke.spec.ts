import { expect, test, type Page } from "@playwright/test";

/**
 * 管理员后台交互冒烟（ADMIN-CONSOLE-UI-0001，生产构建走 MSW）：
 *  ① 平台账号（默认）：顶栏「管理后台」入口可见 → /admin（落租户管理）→ 列表 → Acme 详情 → 余额调整
 *     （确认弹窗显示「当前 → 调整后」确定值）→ 审计页出现「余额调整 20000 → 25000」→ 用量页导出（无筛选
 *     → 422 中文上限提示原样展示）→ 任务监控（默认 failed）重跑 task-f1（弹窗明示「不会重复扣费」）→ 已重新排队；
 *     375 下页面不横滚。
 *  ② 非平台账号（新注册态 platform=0+free）：入口隐藏；直达 /admin → 友好页「仅平台管理员可访问」，
 *     页面不出现任何租户数据；移动端 375 友好页仍在。
 * 全程无 #130 / 无 /api/api 双前缀（Chrome DevTools 口径：Console 0 error）。
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

async function loginAs(page: Page, opts?: { platform?: "0" | "1"; plan?: "huading" | "free" }) {
  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
  await page.evaluate(
    ([platform, plan]) => {
      if (platform) window.localStorage.setItem("hd_mock_platform", platform);
      if (plan) window.localStorage.setItem("hd_mock_plan", plan);
    },
    [opts?.platform ?? "", opts?.plan ?? ""]
  );
  const inputs = page.locator("form input");
  await inputs.nth(0).fill("huading");
  await inputs.nth(1).fill("qa@huading.test");
  await inputs.nth(2).fill("pw123456");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
}

test("① 平台账号：入口 → 租户管理 → 余额调整（前→后 + 审计）→ 导出 422 → 失败任务重跑（不重复扣费）；375 不横滚", async ({ page }) => {
  const g = watch(page);
  await loginAs(page);

  // 顶栏入口（仅 admin_console 显示）→ /admin 落租户管理。
  const entry = page.getByRole("link", { name: "管理后台" });
  await expect(entry).toBeVisible();
  await entry.click();
  await page.waitForURL(/\/admin\/tenants$/, { timeout: 20_000 });
  await expect(page.getByRole("heading", { level: 1, name: "租户管理" })).toBeVisible();
  await expect(page.getByText("贝塔传媒")).toBeVisible({ timeout: 15_000 });

  // Acme 详情 → 余额调整 +5000：确认弹窗必须显示「当前余额 20,000 → 调整后 25,000」。
  const acmeRow = page.locator("tr", { hasText: "Acme 电商" });
  await acmeRow.getByRole("button", { name: "详情" }).click();
  await expect(page.getByText("租户详情")).toBeVisible();
  await page.getByLabel("调整额度（正=充值，负=扣减）").fill("5000");
  await page.getByLabel("理由（必填）").fill("线下打款充值");
  await page.getByRole("button", { name: "余额调整" }).click();
  await expect(page.getByText("当前余额 20,000 → 调整后 25,000")).toBeVisible();
  await page.getByRole("button", { name: "确认调整" }).click();
  await expect(page.getByText("余额已调整")).toBeVisible({ timeout: 15_000 });
  await page.keyboard.press("Escape"); // 关详情弹窗

  // 审计页：余额调整记录（变更前 → 变更后 + 理由）。
  await page.getByRole("link", { name: "审计日志" }).click();
  await page.waitForURL(/\/admin\/audit$/, { timeout: 15_000 });
  await expect(page.getByText("quota_credits_total：20000 → 25000")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("线下打款充值")).toBeVisible();

  // 用量页：无筛选导出 → BE 行数上限 422 中文原样展示。
  await page.getByRole("link", { name: "用量明细" }).click();
  await page.waitForURL(/\/admin\/usage$/, { timeout: 15_000 });
  await expect(page.getByText("导出 CSV")).toBeVisible();
  await page.getByRole("button", { name: "导出 CSV" }).click();
  await expect(page.getByText("导出行数（6）超出上限（3），请缩小筛选范围")).toBeVisible({ timeout: 15_000 });

  // 任务监控：默认 failed；重跑 task-f1（弹窗明示不重复扣费）→ 已重新排队。
  await page.getByRole("link", { name: "任务监控" }).click();
  await page.waitForURL(/\/admin\/tasks$/, { timeout: 15_000 });
  const failedRow = page.locator("tr", { hasText: "task-f1" });
  await expect(failedRow.getByText("PROVIDER_TIMEOUT")).toBeVisible({ timeout: 15_000 });
  await failedRow.getByRole("button", { name: "重跑" }).click();
  await expect(page.getByText(/不会重复扣费/)).toBeVisible();
  await page.getByRole("button", { name: "确认重跑" }).click();
  await expect(page.getByText("已重新排队")).toBeVisible({ timeout: 15_000 });

  // 375：页面不横滚（表格在容器内滚动）。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByRole("heading", { level: 1, name: "任务监控" })).toBeVisible();
  const noBodyHScroll = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  expect(noBodyHScroll, "375 下页面不得横向滚动（表格容器内滚）").toBe(true);

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});

test("② 非平台账号（新注册态）：入口隐藏；/admin → 友好页 + 零租户数据；移动端 375", async ({ page }) => {
  const g = watch(page);
  await loginAs(page, { platform: "0", plan: "free" });

  // 控制台顶栏不出现「管理后台」入口。
  await expect(page.getByRole("button", { name: "生成视频" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("link", { name: "管理后台" })).toHaveCount(0);

  // 直达 /admin → 友好页（正断言先立），不出现任何租户数据/后台导航。
  await page.goto("/admin");
  await expect(page.getByText("仅平台管理员可访问")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("link", { name: "返回工作台" })).toHaveAttribute("href", "/");
  await expect(page.getByText("贝塔传媒")).toHaveCount(0);
  await expect(page.getByText("租户管理")).toHaveCount(0);
  await expect(page.getByText("Acme 电商")).toHaveCount(0);

  // 移动端 375：友好页仍在。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByText("仅平台管理员可访问")).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
