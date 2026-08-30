import { expect, test, type Page } from "@playwright/test";

/**
 * BRAND-VOICE-PICKER-UI-0001 交互冒烟：生产构建(next start)下真走「选我的音色」——数字人口播音色区出现
 * 「我的品牌音色」组（ready 可选 + provider 徽标），选中品牌音色后**生成请求 voice_id 携带品牌音色 id**；
 * 默认仍是系统预设（不选我的音色 → 不回归）。processing 置灰 / 空态跳转 / 缺 provider 兼容由 vitest 确定性覆盖。
 * 需以 NEXT_PUBLIC_USE_MOCK=1 构建后 next start 运行（webServer 已配）。
 */

async function login(page: Page): Promise<string[]> {
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
  return errors;
}

test("口播「选我的音色」：品牌组+徽标；选中后 POST /videos 携带品牌音色 id；默认预设不回归", async ({ page }) => {
  const errors = await login(page);
  // 默认落在数字人口播；音色区出现「我的品牌音色」组 + 系统组。
  // WORKBENCH-KEEPALIVE-UI-0001：面板常驻后口播/电商两份 VoicePicker 同存于 DOM，分组标题 id 已改为 useId 生成
  // （字面量 id 会重复，令第二份的 aria-labelledby 错指隐藏面板那份）→ 改用「面板 scope + role=group 的无障碍名」
  // 定位：既避开面包屑同名入口（brandVoice.entry 与 pickerBrandGroup 都叫「我的品牌音色」），又顺带验证
  // aria-labelledby 关联仍正确。
  const avatar = page.getByTestId("panel-avatar_talk");
  await expect(avatar.getByRole("group", { name: "我的品牌音色" })).toBeVisible({ timeout: 15_000 });
  await expect(avatar.getByRole("group", { name: "系统音色" })).toBeVisible();
  // ready 品牌音色 + provider 徽标（豆包 / CosyVoice）。
  await expect(page.getByText("我的主播音")).toBeVisible();
  await expect(page.getByText("豆包")).toBeVisible();
  await expect(page.getByText("免费复刻音")).toBeVisible();
  await expect(page.getByText("CosyVoice")).toBeVisible();

  // 不回归：默认选中系统预设「知性女声」（voiceList[0]），非品牌音色。
  const presetBtn = page.locator('button:has-text("知性女声")');
  await expect(presetBtn).toHaveAttribute("aria-pressed", "true");
  const brandBtn = page.locator('button:has-text("我的主播音")');
  await expect(brandBtn).toHaveAttribute("aria-pressed", "false");

  // 填主题/口播文案 + 传形象（品牌音色权威报价要求真实可计费文案）。
  await page.locator("#video-topic").fill("咖啡测评");
  await page.locator("#video-script").fill("今天用这杯咖啡演示我的品牌音色。");
  await page.locator('input[type="file"]').first().setInputFiles({
    name: "avatar.png",
    mimeType: "image/png",
    buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
  });

  // 选品牌音色 → 选中态。
  await brandBtn.click();
  await expect(brandBtn).toHaveAttribute("aria-pressed", "true");
  await expect(presetBtn).toHaveAttribute("aria-pressed", "false");

  // 生成 → 服务端权威报价确认 → POST /videos；验收响应、品牌音色 id 与两条必需计费头。
  await page.getByRole("button", { name: "生成视频" }).click();
  await expect(page.getByText("服务端应付积分")).toBeVisible({ timeout: 15_000 });
  const [videoResponse] = await Promise.all([
    page.waitForResponse((response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/videos"
    ),
    page.getByRole("button", { name: "确认并继续" }).click()
  ]);
  expect(videoResponse.status()).toBe(202);
  const videoRequest = videoResponse.request();
  expect((videoRequest.postDataJSON() as { voice_id?: string }).voice_id).toBe("bv-ready-1");
  expect(await videoRequest.headerValue("X-Huading-Quote")).toMatch(/^mock-video_create-quote-/);
  expect(await videoRequest.headerValue("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/i);

  expect(errors, `page errors：\n${errors.join("\n")}`).toEqual([]);
});

test("电商带货「选我的音色」+ 移动端显示：品牌组与 ready 音色可见，无 #130", async ({ page }) => {
  const errors = await login(page);
  // 切到电商带货。KEEPALIVE：口播面板此时仍挂载（隐藏），故一律限定到电商面板 scope。
  await page.getByRole("button", { name: "电商带货" }).click();
  const ecom = page.getByTestId("panel-seedance_i2v");
  await expect(ecom.getByRole("group", { name: "我的品牌音色" })).toBeVisible({ timeout: 15_000 });
  await expect(ecom.getByText("我的主播音")).toBeVisible();

  // 移动端（375）：品牌组与 ready 音色仍可见，不溢出/白屏。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(ecom.getByRole("group", { name: "我的品牌音色" })).toBeVisible();
  await expect(ecom.getByText("我的主播音")).toBeVisible();

  expect(errors, `page errors：\n${errors.join("\n")}`).toEqual([]);
});
