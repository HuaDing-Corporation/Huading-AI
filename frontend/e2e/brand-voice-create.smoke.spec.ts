import { expect, test } from "@playwright/test";

/**
 * BRAND-VOICE-PICKER-UI-0001-FIX1（范围4）交互冒烟：生产构建下品牌音色创建的「克隆通路选择 + 人工订单
 * 报价确认」——/brand-voices 两档卡切换（缺省 doubao，与现状一致）→ 上传音频 → 提交 doubao 人工订单，
 * 服务端报价 30000 积分并冻结等待人工交付（不声称自动注册）→ 移动端两档和订单状态仍可见。
 * 需以 NEXT_PUBLIC_USE_MOCK=1 构建后 next start 运行（webServer 已配）。
 */
test("创建·两档切换 + 缺省 doubao 人工订单冻结 + 移动端显示", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(String(err?.message ?? err)));
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Minified React error #130|error #130|client-side exception/.test(t)) errors.push(t);
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

  // 工作台面包屑「我的品牌音色」入口 → /brand-voices（SPA 导航，SW 持续接管）。
  await page.getByRole("link", { name: /我的品牌音色/ }).click();
  await page.waitForURL(/\/brand-voices$/, { timeout: 30_000 });

  // 两档通路卡可见；**缺省 doubao 选中**（与现状一致），cosyvoice 未选。
  const doubaoCard = page.locator('button:has-text("升级版 VIP 人工交付音色")');
  const cosyCard = page.locator('button:has-text("免费开通私人专属音色")');
  await expect(doubaoCard).toBeVisible({ timeout: 15_000 });
  await expect(cosyCard).toBeVisible();
  await expect(doubaoCard).toHaveAttribute("aria-pressed", "true");
  await expect(cosyCard).toHaveAttribute("aria-pressed", "false");

  // 两档切换：选 cosyvoice → 选中；切回 doubao → 选中。
  await cosyCard.click();
  await expect(cosyCard).toHaveAttribute("aria-pressed", "true");
  await expect(doubaoCard).toHaveAttribute("aria-pressed", "false");
  await doubaoCard.click();
  await expect(doubaoCard).toHaveAttribute("aria-pressed", "true");

  // 上传音频 + 名称 + 授权（满足创建前置）。
  await page.locator("#brand-voice-audio").setInputFiles({
    name: "voice.mp3",
    mimeType: "audio/mpeg",
    buffer: Buffer.from("xxxxxxxxxx")
  });
  await page.locator("#brand-voice-name").fill("我的VIP音");
  await page.getByRole("checkbox").check();

  // doubao 付费 → 服务端报价后提交人工订单；201 仅代表冻结并待人工交付，不代表自动 provider 注册。
  await page.getByRole("button", { name: "提交开通" }).click();
  await expect(page.getByText("服务端应付积分")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("30000 积分").last()).toBeVisible();
  const [submitResponse] = await Promise.all([
    page.waitForResponse((response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/brand-voice-orders"
    ),
    page.getByRole("button", { name: "确认并提交人工开通" }).click()
  ]);
  expect(submitResponse.status()).toBe(201);
  const order = page.locator("li", { hasText: "我的VIP音" });
  await expect(order).toBeVisible({ timeout: 15_000 });
  await expect(order.getByText("已冻结 30000 积分，等待平台人工交付；订单不自动超时且无法取消")).toBeVisible();

  // 移动端（375）：两档卡及已冻结人工订单仍可见、不溢出/白屏。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(doubaoCard).toBeVisible();
  await expect(cosyCard).toBeVisible();
  await expect(order).toBeVisible();

  expect(errors, `page errors：\n${errors.join("\n")}`).toEqual([]);
});
