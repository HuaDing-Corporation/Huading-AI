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
  await page.goto("/");
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
  // 默认落在数字人口播；音色区出现「我的品牌音色」组 + 系统组（用 group 标题 id，避开面包屑同名入口）。
  await expect(page.locator("#voice-group-brand")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("#voice-group-standard")).toBeVisible();
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

  // 填主题 + 传形象（满足生成前置）。
  await page.locator("#video-topic").fill("咖啡测评");
  await page.locator('input[type="file"]').first().setInputFiles({
    name: "avatar.png",
    mimeType: "image/png",
    buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
  });

  // 选品牌音色 → 选中态。
  await brandBtn.click();
  await expect(brandBtn).toHaveAttribute("aria-pressed", "true");
  await expect(presetBtn).toHaveAttribute("aria-pressed", "false");

  // 捕获生成请求体 voice_id。
  let voiceId: string | null = null;
  page.on("request", (req) => {
    if (req.method() === "POST" && new URL(req.url()).pathname.endsWith("/api/v1/videos")) {
      try {
        voiceId = (req.postDataJSON() as { voice_id?: string })?.voice_id ?? null;
      } catch {
        /* ignore */
      }
    }
  });

  // 生成 → 确认窗 → 确定，触发 POST /videos。
  await page.getByRole("button", { name: "生成视频" }).click();
  await page.getByRole("button", { name: "确定" }).click();
  await expect.poll(() => voiceId, { timeout: 15_000 }).toBe("bv-ready-1");

  expect(errors, `page errors：\n${errors.join("\n")}`).toEqual([]);
});

test("电商带货「选我的音色」+ 移动端显示：品牌组与 ready 音色可见，无 #130", async ({ page }) => {
  const errors = await login(page);
  // 切到电商带货。
  await page.getByRole("button", { name: "电商带货" }).click();
  await expect(page.locator("#voice-group-brand")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("我的主播音")).toBeVisible();

  // 移动端（375）：品牌组与 ready 音色仍可见，不溢出/白屏。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.locator("#voice-group-brand")).toBeVisible();
  await expect(page.getByText("我的主播音")).toBeVisible();

  expect(errors, `page errors：\n${errors.join("\n")}`).toEqual([]);
});
