import { expect, test, type Locator, type Page } from "@playwright/test";

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

async function createDoubaoOrder(page: Page, name: string) {
  if (page.url().includes("/admin")) {
    await page.getByRole("link", { name: "返回控制台" }).click();
    await page.waitForURL("http://localhost:3100/");
  }
  if (!page.url().endsWith("/brand-voices")) {
    await page.getByRole("link", { name: "我的品牌音色" }).click();
    await page.waitForURL(/\/brand-voices$/);
  }
  await page.locator("#brand-voice-audio").setInputFiles({ name: `${name}.mp3`, mimeType: "audio/mpeg", buffer: Buffer.from("voice-audio") });
  await page.locator("#brand-voice-name").fill(name);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "提交开通" }).click();
  await expect(page.getByText("服务端应付积分")).toBeVisible();
  await expect(page.getByText("30000 积分").last()).toBeVisible();
  await page.getByRole("button", { name: "确认并提交人工开通" }).click();
  await expect(page.getByText("已冻结 30000 积分，等待平台人工交付；订单不自动超时且无法取消").first()).toBeVisible();
}

async function openAdminOrder(page: Page, name: string): Promise<Locator> {
  await page.getByRole("link", { name: "管理后台" }).click();
  await page.waitForURL(/\/admin(?:\/tenants)?$/);
  await page.getByRole("link", { name: "人工音色订单" }).click();
  await page.waitForURL(/\/admin\/brand-voice-orders$/);
  const row = page.locator("li", { hasText: name });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "查看订单" }).click();
  await expect(page.locator('audio[aria-label="订单源音频"]')).toBeVisible();
  return row;
}

test("manual Doubao orders transition awaiting to fulfilled and rejected with payer isolation", async ({ page }) => {
  test.setTimeout(120_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().includes("Failed to load resource: net::ERR_FAILED")) errors.push(message.text());
  });
  await login(page);

  await createDoubaoOrder(page, "E2E 待交付音色");
  await expect(page.getByText("他人购买音色", { exact: true })).toHaveCount(0);
  await openAdminOrder(page, "E2E 待交付音色");
  await page.locator("#provider-voice-id").fill("S_e2e_fulfilled_001");
  await page.getByRole("button", { name: "确认交付" }).click();
  await expect(page.getByText("已交付并结算 30000 积分")).toBeVisible();
  await expect(page.getByRole("button", { name: "确认交付" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "拒绝订单" })).toBeDisabled();

  await createDoubaoOrder(page, "E2E 待拒绝音色");
  await openAdminOrder(page, "E2E 待拒绝音色");
  await page.locator("#rejection-reason").fill("授权音频不符合交付要求");
  await page.getByRole("button", { name: "拒绝订单" }).click();
  await expect(page.getByText("已拒绝：授权音频不符合交付要求")).toBeVisible();

  await page.getByRole("link", { name: "返回控制台" }).click();
  await page.getByRole("link", { name: "我的品牌音色" }).click();
  await page.getByRole("button", { name: "查询退款状态" }).click();
  const rejectedOrder = page.locator("li", { hasText: "E2E 待拒绝音色" });
  await expect(rejectedOrder.getByText("订单已拒绝：授权音频不符合交付要求")).toBeVisible();
  await expect(rejectedOrder.getByText("原订阅冻结已释放，积分现在可用")).toBeVisible();
  await expect(page.getByText("他人购买音色", { exact: true })).toHaveCount(0);

  await page.getByRole("link", { name: "管理后台" }).click();
  await page.getByRole("link", { name: "音色槽位" }).click();
  await expect(page.getByText("旧槽位分配入口已退役。本页仅用于核对平台/租户登记与冲突，不提供写操作。")).toBeVisible();
  await expect(page.getByRole("button", { name: /分配|保存|提交/ })).toHaveCount(0);
  expect(errors).toEqual([]);
});
