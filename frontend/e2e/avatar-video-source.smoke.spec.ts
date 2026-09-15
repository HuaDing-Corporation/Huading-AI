import { expect, test, type Page } from "@playwright/test";

/**
 * AVATAR-VIDEO-SOURCE-UI-0001 交互冒烟：生产构建下数字人口播形象源「照片/视频二选一」——
 * ① 照片默认零回归：不切来源，生成请求带 avatar_asset_id、不带 avatar_video_asset_id；
 * ② 视频源：切「本人出镜视频」→ 上传真 MP4(过客户端元数据预检)→ 生成请求带 avatar_video_asset_id(互斥)；
 * ③ 移动端(375)两档切换可见。无 #130。fixture: e2e/fixtures/avatar-sample.mp4(640×480/3s，过 ≤10s+360–1080p)。
 * 需以 NEXT_PUBLIC_USE_MOCK=1 构建后 next start 运行（webServer 已配）。
 */
const AVATAR_PNG = { name: "a.png", mimeType: "image/png", buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]) };
const VIDEO_FIXTURE = "e2e/fixtures/avatar-sample.mp4";

async function login(page: Page): Promise<{ errors: () => string[]; videoBody: () => Record<string, unknown> | null }> {
  const errors: string[] = [];
  let body: Record<string, unknown> | null = null;
  page.on("pageerror", (err) => errors.push(String(err?.message ?? err)));
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Minified React error #130|error #130|client-side exception/.test(t)) errors.push(t);
  });
  page.on("request", (req) => {
    if (req.method() === "POST" && new URL(req.url()).pathname.endsWith("/api/v1/videos")) {
      try {
        body = req.postDataJSON() as Record<string, unknown>;
      } catch {
        /* ignore */
      }
    }
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
  return { errors: () => errors, videoBody: () => body };
}

async function generate(page: Page): Promise<void> {
  const btn = page.getByRole("button", { name: /生成视频/ });
  await expect(btn).toBeEnabled({ timeout: 15_000 });
  await btn.click();
  await expect(page.getByRole("button", { name: "确定", exact: true })).toBeDisabled();
  await page.getByRole("checkbox", { name: /我已阅读并同意/ }).check();
  await page.getByRole("button", { name: "确定" }).click();
}

test("照片默认零回归：不切来源 → 生成请求带 avatar_asset_id、不带 avatar_video_asset_id", async ({ page }) => {
  const g = await login(page);
  // 默认落数字人口播；照片档默认选中。
  await expect(page.getByRole("button", { name: "照片", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "本人出镜视频", exact: true })).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByText("HeyGen Avatar IV", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("目标模型 · 服务可用性以提交校验为准")).toBeVisible();

  await page.locator("#video-topic").fill("咖啡评测");
  await page.locator('input[type="file"]#avatar-image').setInputFiles(AVATAR_PNG);
  await expect(page.getByText("已上传，可生成")).toBeVisible({ timeout: 15_000 });
  await generate(page);

  await expect.poll(() => g.videoBody()?.avatar_asset_id, { timeout: 15_000 }).toBeTruthy();
  expect(g.videoBody()?.avatar_video_asset_id).toBeUndefined();
  expect(g.videoBody()?.voice_id).toBeTruthy();
  expect(g.videoBody()?.avatar_provider).toBeUndefined();
  expect(g.videoBody()?.avatar_duration_policy).toBe("145s-no-refund-v1");
  expect(g.videoBody()?.avatar_duration_policy_token).toBeTruthy();
  expect(g.errors(), g.errors().join("\n")).toEqual([]);
});

test("视频源：切「本人出镜视频」→ 传 MP4(过预检) → 生成请求带 avatar_video_asset_id(互斥)；移动端两档可见", async ({ page }) => {
  const g = await login(page);
  await page.getByRole("button", { name: "本人出镜视频", exact: true }).click();
  await expect(page.getByRole("button", { name: "本人出镜视频", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("HeyGen Precision", { exact: false }).first()).toBeVisible();

  await page.locator("#video-topic").fill("咖啡评测");
  await page.locator('input[type="file"]#avatar-video').setInputFiles(VIDEO_FIXTURE);
  // 过元数据预检 + 上传成功。
  await expect(page.getByText("已上传，可生成")).toBeVisible({ timeout: 20_000 });
  await generate(page);

  await expect.poll(() => g.videoBody()?.avatar_video_asset_id, { timeout: 15_000 }).toBeTruthy();
  expect(g.videoBody()?.avatar_asset_id).toBeUndefined();
  expect(g.videoBody()?.voice_id).toBeTruthy();
  expect(g.videoBody()?.avatar_provider).toBeUndefined();
  expect(g.videoBody()?.avatar_duration_policy).toBe("145s-no-refund-v1");
  expect(g.videoBody()?.avatar_duration_policy_token).toBeTruthy();

  // 移动端（375）：两档切换仍可见。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByRole("button", { name: "照片", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "本人出镜视频", exact: true })).toBeVisible();

  expect(g.errors(), g.errors().join("\n")).toEqual([]);
});

for (const source of ["photo", "video"] as const) {
  for (const billed of [false, true]) {
    test(`145s failed/settled UI: ${source}, billed=${billed}`, async ({ page }) => {
      const g = await login(page);
      await page.setViewportSize({ width: billed ? 1280 : 375, height: 812 });
      await page.evaluate(() => localStorage.setItem("hd_mock_avatar_145_failure", "1"));
      await page.locator("#video-topic").fill("145秒失败账务演示");
      await page.locator("#video-script").fill("四字文案");
      if (source === "video") {
        await page.getByRole("button", { name: "本人出镜视频", exact: true }).click();
        await page.locator("#avatar-video").setInputFiles(VIDEO_FIXTURE);
      } else {
        await page.locator("#avatar-image").setInputFiles(AVATAR_PNG);
      }
      await expect(page.getByText("已上传，可生成")).toBeVisible();
      if (billed) {
        await page.getByRole("button", { name: /免费复刻音/ }).click();
      }
      await page.getByRole("button", { name: "生成视频", exact: true }).click();
      const submit = page.getByRole("button", { name: billed ? "确认并继续" : "确定", exact: true });
      await expect(submit).toBeDisabled();
      const checkbox = page.getByRole("checkbox", { name: /我已阅读并同意/ });
      await expect(checkbox).toBeEnabled();
      await checkbox.focus();
      await page.keyboard.press("Space");
      await expect(checkbox).toBeChecked();
      await expect(submit).toBeEnabled();
      const overflow = await page.getByRole("dialog").evaluate((dialog) => ({ client: dialog.clientWidth, scroll: dialog.scrollWidth }));
      expect(overflow.scroll).toBeLessThanOrEqual(overflow.client + 1);
      if (billed) {
        // The consent notice must not flex-shrink the hidden-overflow price box:
        // at 375px this used to crop the payable amount even with zero horizontal overflow.
        await page.setViewportSize({ width: 375, height: 812 });
        const priceLayout = page.getByTestId("billing-price-layout");
        const priceHeight = await priceLayout.evaluate((element) => ({
          client: element.clientHeight, scroll: element.scrollHeight
        }));
        expect(priceHeight.scroll).toBeLessThanOrEqual(priceHeight.client + 1);
        await page.getByText("服务端应付积分", { exact: true }).scrollIntoViewIfNeeded();
        await expect(page.getByText("服务端应付积分", { exact: true })).toBeInViewport();
      }
      const response = page.waitForResponse((res) => res.request().method() === "POST" && new URL(res.url()).pathname === "/api/v1/videos");
      await submit.click();
      const accepted = await (await response).json() as { data: { id: string } };
      await expect(page.getByText(/生成失败（超145秒，费用不退）。已结算 \d+ 积分/).first()).toBeVisible();
      await expect(page.getByRole("button", { name: "重试", exact: true })).toHaveCount(0);
      // Client navigation preserves the in-page MSW store, as other mock E2E do.
      // A full reload creates a new fixture store and cannot look up this task.
      await page.getByRole("button", { name: "查看详情", exact: true }).first().click();
      await expect(page).toHaveURL(new RegExp(`/videos/${accepted.data.id}$`));
      await expect(page.getByText(/生成失败（超145秒，费用不退）。已结算 \d+ 积分/)).toBeVisible();
      expect(g.errors()).toEqual([]);
    });
  }
}
