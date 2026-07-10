import { expect, test } from "@playwright/test";

/**
 * BRAND-VOICE-PICKER-UI-0001-FIX1（范围4）交互冒烟：生产构建下品牌音色创建的「克隆通路选择 + 付费扣费
 * 确认」——/brand-voices 两档卡切换（缺省 doubao，与现状一致）→ 上传音频 → 选 doubao(付费)点创建**先弹
 * 30000 积分扣费确认**（cosyvoice 免费直建）→ 移动端两档仍可见。证 Radix 确认弹窗在 prod 正常、无 #130。
 * 需以 NEXT_PUBLIC_USE_MOCK=1 构建后 next start 运行（webServer 已配）。
 */
test("创建·两档切换 + 缺省 doubao 扣费确认 + 移动端显示", async ({ page }) => {
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
  const doubaoCard = page.locator('button:has-text("升级版 VIP 永久高端定制音色")');
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

  // doubao 付费 → 点创建先弹扣费确认（30000 积分，尚未真正创建）。
  await page.getByRole("button", { name: "创建品牌音色" }).click();
  await expect(page.getByText("确认开通高端定制音色？")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/将消耗 30000 积分/)).toBeVisible();

  // 移动端（375）：关弹窗后两档卡仍可见、不溢出/白屏。
  await page.getByRole("button", { name: "取消" }).click();
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(doubaoCard).toBeVisible();
  await expect(cosyCard).toBeVisible();

  expect(errors, `page errors：\n${errors.join("\n")}`).toEqual([]);
});
