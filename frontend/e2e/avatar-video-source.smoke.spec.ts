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
  await page.getByRole("button", { name: "确定" }).click();
}

test("照片默认零回归：不切来源 → 生成请求带 avatar_asset_id、不带 avatar_video_asset_id", async ({ page }) => {
  const g = await login(page);
  // 默认落数字人口播；照片档默认选中。
  await expect(page.getByRole("button", { name: "照片", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "本人出镜视频", exact: true })).toHaveAttribute("aria-pressed", "false");

  await page.locator("#video-topic").fill("咖啡评测");
  await page.locator('input[type="file"]#avatar-image').setInputFiles(AVATAR_PNG);
  await expect(page.getByText("已上传，可生成")).toBeVisible({ timeout: 15_000 });
  await generate(page);

  await expect.poll(() => g.videoBody()?.avatar_asset_id, { timeout: 15_000 }).toBeTruthy();
  expect(g.videoBody()?.avatar_video_asset_id).toBeUndefined();
  expect(g.errors(), g.errors().join("\n")).toEqual([]);
});

test("视频源：切「本人出镜视频」→ 传 MP4(过预检) → 生成请求带 avatar_video_asset_id(互斥)；移动端两档可见", async ({ page }) => {
  const g = await login(page);
  await page.getByRole("button", { name: "本人出镜视频", exact: true }).click();
  await expect(page.getByRole("button", { name: "本人出镜视频", exact: true })).toHaveAttribute("aria-pressed", "true");

  await page.locator("#video-topic").fill("咖啡评测");
  await page.locator('input[type="file"]#avatar-video').setInputFiles(VIDEO_FIXTURE);
  // 过元数据预检 + 上传成功。
  await expect(page.getByText("已上传，可生成")).toBeVisible({ timeout: 20_000 });
  await generate(page);

  await expect.poll(() => g.videoBody()?.avatar_video_asset_id, { timeout: 15_000 }).toBeTruthy();
  expect(g.videoBody()?.avatar_asset_id).toBeUndefined();

  // 移动端（375）：两档切换仍可见。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByRole("button", { name: "照片", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "本人出镜视频", exact: true })).toBeVisible();

  expect(g.errors(), g.errors().join("\n")).toEqual([]);
});
