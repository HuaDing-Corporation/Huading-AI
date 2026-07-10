import { expect, test, type Page } from "@playwright/test";

/**
 * UI-COMINGSOON-TENANT-RENAME-0001 交互冒烟（生产构建 next start）：
 * 范围一「即将上线」：模板中心/品牌库/发布中心/团队 四项导航名带「（即将上线）」→ 点进去统一占位页「该功能即将上线」；
 *   零回归：工作台/批量生产照常（非占位）。范围二「租户→用户」：登录页显示「用户标识」（无「租户」）。移动端 375 导航可见。
 * 全程无 #130 白屏 / 无 /api/api 双前缀。需以 NEXT_PUBLIC_USE_MOCK=1 构建后 next start 运行（webServer 已配）。
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

  await page.goto("/");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
  if (await page.getByRole("button", { name: "登录" }).isVisible().catch(() => false)) {
    // 范围二：登录页「租户」→「用户」（标识字段标签、副标题）。
    await expect(page.getByText("用户标识 (tenant slug)")).toBeVisible();
    await expect(page.getByText("输入用户与账号以继续")).toBeVisible();
    await expect(page.getByText("租户", { exact: false })).toHaveCount(0);
    const inputs = page.locator("form input");
    await inputs.nth(0).fill("huading");
    await inputs.nth(1).fill("qa@huading.test");
    await inputs.nth(2).fill("pw123456");
    await page.getByRole("button", { name: "登录" }).click();
    await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
  }
  return { errors: () => errors, doublePrefix: () => doublePrefix };
}

test("4 板块「即将上线」→ 点进统一占位页；工作台/批量生产零回归；移动端可见", async ({ page }) => {
  const g = await login(page);

  const soon: { name: string; path: string }[] = [
    { name: "模板中心（即将上线）", path: "/templates" },
    { name: "品牌库（即将上线）", path: "/brand-library" },
    { name: "发布中心（即将上线）", path: "/publish" },
    { name: "团队（即将上线）", path: "/team" }
  ];
  for (const { name, path } of soon) {
    await page.getByRole("link", { name, exact: true }).click();
    await page.waitForURL(`http://localhost:3100${path}`, { timeout: 15_000 });
    // 统一友好占位页。
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await expect(page.getByText("该功能即将上线")).toBeVisible();
  }

  // 零回归：批量生产照常（真页面，非占位）。
  await page.getByRole("link", { name: "批量生产", exact: true }).click();
  await page.waitForURL("http://localhost:3100/batch", { timeout: 15_000 });
  await expect(page.getByText("该功能即将上线")).toHaveCount(0);

  // 工作台照常。
  await page.getByRole("link", { name: "工作台", exact: true }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 15_000 });
  await expect(page.getByText("该功能即将上线")).toHaveCount(0);

  // 移动端（375）：占位板块导航仍可见。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByRole("link", { name: "模板中心（即将上线）", exact: true })).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
