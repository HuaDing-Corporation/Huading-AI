import { expect, test, type Locator, type Page } from "@playwright/test";

type Payer = "default" | "other";

const OTHER_PAYER = {
  tenant: { id: "ten-other-e2e", slug: "other-e2e", name: "隔离租户" },
  user: { id: "u-other-e2e", tenant_id: "ten-other-e2e", email: "other@e2e.test", full_name: "隔离付款人", role: "admin" }
};
async function switchPayer(page: Page, payer: Payer) {
  await page.evaluate(({ payer, other }) => {
    if (payer === "other") {
      localStorage.setItem("hd_mock_registered", JSON.stringify(other));
    } else {
      localStorage.removeItem("hd_mock_registered");
    }
  }, { payer, other: OTHER_PAYER });
}

async function payerSnapshot(page: Page) {
  return page.evaluate(async () => {
    const get = (path: string) => fetch(`http://localhost:8000${path}`).then((response) => response.json()).then((payload) => payload.data);
    const [identity, quota, orders, voices] = await Promise.all([
      get("/api/v1/auth/me"),
      get("/api/v1/quota"),
      get("/api/v1/brand-voice-orders"),
      get("/api/v1/brand-voices")
    ]);
    return {
      identity,
      balance: `当前余额 ${quota.remaining}/${quota.total}`,
      held: `人工交付冻结 ${quota.manual_fulfillment_held_credits}`,
      orderTexts: orders.items.map((order: { requested_name: string }) => order.requested_name),
      voiceNames: voices.items.map((voice: { name: string }) => voice.name)
    };
  });
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
  await page.getByRole("button", { name: /升级版 VIP 人工交付音色/ }).click();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "提交开通" }).click();
  await expect(page.getByText("服务端应付积分")).toBeVisible();
  await expect(page.getByText("30000 积分").last()).toBeVisible();
  const [submitResponse] = await Promise.all([
    page.waitForResponse((response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/brand-voice-orders"
    ),
    page.getByRole("button", { name: "确认并提交人工开通" }).click()
  ]);
  expect(submitResponse.status()).toBe(201);
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
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("requestfailed", (request) => {
    errors.push(`requestfailed ${request.method()} ${new URL(request.url()).pathname}: ${request.failure()?.errorText ?? "unknown"}`);
  });
  await login(page);
  await page.getByRole("link", { name: "我的品牌音色" }).click();
  await page.waitForURL(/\/brand-voices$/);

  const payerAInitial = await payerSnapshot(page);
  expect(payerAInitial.identity).toMatchObject({
    tenant: { id: "ten-mock" },
    user: { id: "u-mock", tenant_id: "ten-mock" }
  });
  expect(payerAInitial).toMatchObject({ balance: "当前余额 844/1000", held: "人工交付冻结 30000" });
  expect(payerAInitial.orderTexts.join("\n")).toContain("待人工交付音色");

  await switchPayer(page, "other");
  const payerBInitial = await payerSnapshot(page);
  expect(payerBInitial.identity).toMatchObject({
    tenant: { id: OTHER_PAYER.tenant.id },
    user: { id: OTHER_PAYER.user.id, tenant_id: OTHER_PAYER.tenant.id, email: OTHER_PAYER.user.email }
  });
  expect(payerBInitial).toMatchObject({ balance: "当前余额 844/1000", held: "人工交付冻结 0", orderTexts: [] });
  expect(payerBInitial.orderTexts.join("\n")).not.toContain("待人工交付音色");
  await page.getByRole("button", { name: "查询退款状态" }).click();
  await expect(page.getByText("暂无人工开通订单", { exact: true })).toBeVisible();

  await switchPayer(page, "default");
  await page.getByRole("button", { name: "查询退款状态" }).click();
  await expect(page.getByText("待人工交付音色", { exact: true })).toBeVisible();

  await createDoubaoOrder(page, "E2E 待交付音色");
  expect(await payerSnapshot(page)).toMatchObject({ held: "人工交付冻结 60000", orderTexts: expect.arrayContaining(["E2E 待交付音色"]) });
  await expect(page.getByText("他人购买音色", { exact: true })).toHaveCount(0);
  await openAdminOrder(page, "E2E 待交付音色");
  await page.locator("#provider-voice-id").fill("S_e2e_fulfilled_001");
  await page.getByRole("button", { name: "确认交付" }).click();
  await expect(page.getByText("已交付并结算 30000 积分")).toBeVisible();
  await expect(page.getByRole("button", { name: "确认交付" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "拒绝订单" })).toBeDisabled();

  await page.getByRole("link", { name: "返回控制台" }).click();
  await page.getByRole("link", { name: "我的品牌音色" }).click();
  expect(await payerSnapshot(page)).toMatchObject({ held: "人工交付冻结 30000" });
  await expect(page.getByText("E2E 待交付音色", { exact: true })).toBeVisible();
  await expect(page.getByText("已交付并结算 30000 积分", { exact: true })).toBeVisible();

  await createDoubaoOrder(page, "E2E 待拒绝音色");
  expect(await payerSnapshot(page)).toMatchObject({ held: "人工交付冻结 60000", orderTexts: expect.arrayContaining(["E2E 待拒绝音色"]) });
  await openAdminOrder(page, "E2E 待拒绝音色");
  await page.locator("#rejection-reason").fill("授权音频不符合交付要求");
  await page.getByRole("button", { name: "拒绝订单" }).click();
  await expect(page.getByText("已拒绝：授权音频不符合交付要求")).toBeVisible();

  await page.getByRole("link", { name: "返回控制台" }).click();
  await page.getByRole("link", { name: "我的品牌音色" }).click();
  expect(await payerSnapshot(page)).toMatchObject({ held: "人工交付冻结 30000" });
  await page.getByRole("button", { name: "查询退款状态" }).click();
  const rejectedOrder = page.locator("li", { hasText: "E2E 待拒绝音色" });
  await expect(rejectedOrder.getByText("订单已拒绝：授权音频不符合交付要求")).toBeVisible();
  await expect(rejectedOrder.getByText("原订阅冻结已释放，积分现在可用")).toBeVisible();
  await expect(page.getByText("他人购买音色", { exact: true })).toHaveCount(0);

  const payerAFinal = await payerSnapshot(page);
  expect(payerAFinal.identity.user.id).toBe("u-mock");
  expect(payerAFinal).toMatchObject({ balance: payerAInitial.balance, held: payerAInitial.held });
  expect(payerAFinal.orderTexts.join("\n")).toContain("E2E 待交付音色");
  expect(payerAFinal.orderTexts.join("\n")).toContain("E2E 待拒绝音色");

  await switchPayer(page, "other");
  const payerBFinal = await payerSnapshot(page);
  expect(payerBFinal.identity.user.id).toBe(OTHER_PAYER.user.id);
  expect(payerBFinal).toEqual(payerBInitial);
  expect(payerBFinal.orderTexts.join("\n")).not.toMatch(/E2E 待交付音色|E2E 待拒绝音色/);
  expect(payerBFinal.voiceNames).not.toContain("E2E 待交付音色");
  await page.getByRole("button", { name: "查询退款状态" }).click();
  const payerBOrderCard = page.getByRole("heading", { name: "人工开通订单" }).locator("..").locator("..");
  await expect(payerBOrderCard.getByText("E2E 待交付音色", { exact: true })).toHaveCount(0);
  await expect(payerBOrderCard.getByText("E2E 待拒绝音色", { exact: true })).toHaveCount(0);
  await expect(page.getByText("免费复刻音", { exact: true })).toBeVisible();

  await switchPayer(page, "default");

  await page.getByRole("link", { name: "管理后台" }).click();
  await page.getByRole("link", { name: "音色槽位" }).click();
  await expect(page.getByText("旧槽位分配入口已退役。本页仅用于核对平台/租户登记与冲突，不提供写操作。")).toBeVisible();
  await expect(page.getByRole("button", { name: /分配|保存|提交/ })).toHaveCount(0);
  expect(errors).toEqual([]);
});
