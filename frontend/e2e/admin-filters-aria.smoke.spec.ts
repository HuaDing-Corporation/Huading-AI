import { expect, test, type Page } from "@playwright/test";

/**
 * SELECT-ARIA-LABEL-FIX-0001 · 真 Chromium 无障碍树承重（testing-library 的 getByRole 走 jsdom，
 * 与浏览器真实 a11y 树不完全等价——这里用 Playwright getByRole 查 Chromium 真 AX 树）。
 * 修前：这些筛选器的 combobox 可及名为空（只听得到当前值）；修后：aria-label 透传 → 有确定可及名。
 */
async function loginPlatform(page: Page) {
  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
  const inputs = page.locator("form input");
  await inputs.nth(0).fill("huading");
  await inputs.nth(1).fill("qa@huading.test");
  await inputs.nth(2).fill("pw123456");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
}

test("admin 筛选器在真 Chromium a11y 树里有确定可及名，音色槽位为冻结只读视图", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e?.message ?? e)));
  await loginPlatform(page);

  await page.getByRole("link", { name: "管理后台" }).click();
  await page.waitForURL(/\/admin\/tenants$/, { timeout: 20_000 });
  // 租户管理：套餐筛选器可及名（真 AX 树）。
  await expect(page.getByRole("combobox", { name: "套餐" })).toBeVisible({ timeout: 15_000 });

  await page.getByRole("link", { name: "审计日志" }).click();
  await page.waitForURL(/\/admin\/audit$/, { timeout: 15_000 });
  await expect(page.getByRole("combobox", { name: "动作" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("combobox", { name: "被操作租户" })).toBeVisible();

  await page.getByRole("link", { name: "任务监控" }).click();
  await page.waitForURL(/\/admin\/tasks$/, { timeout: 15_000 });
  await expect(page.getByRole("combobox", { name: "状态" })).toBeVisible({ timeout: 15_000 });

  await page.getByRole("link", { name: "用量明细" }).click();
  await page.waitForURL(/\/admin\/usage$/, { timeout: 15_000 });
  await expect(page.getByRole("combobox", { name: "capability" })).toBeVisible({ timeout: 15_000 });

  await page.getByRole("link", { name: "音色槽位" }).click();
  await page.waitForURL(/\/admin\/voice-slots$/, { timeout: 15_000 });
  await expect(page.getByText("旧槽位分配入口已退役。本页仅用于核对平台/租户登记与冲突，不提供写操作。")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("columnheader", { name: "来源" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "冲突状态" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "租户" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /分配|保存|提交/ })).toHaveCount(0);

  expect(errors, `page errors：\n${errors.join("\n")}`).toEqual([]);
});
