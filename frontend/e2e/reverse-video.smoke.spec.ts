import { expect, test, type Page } from "@playwright/test";

/**
 * VIDEO-REVERSE-PROMPT-UI-0001 交互冒烟（生产构建 next start，真走 MSW 视频异步）：提示词反推 切「视频」→ 上传 MP4 →
 * 反推(计费门 100 积分/确认) → 202+轮询 → 结果（视频分析 video_analysis 在上 + Seedance 提示词在下）→「带入·数字人口播」
 * 预填目标表单。图片模式零回归（切回图片仍在）。移动端(375)来源二选一可见。全程无 #130 / 无 /api/api 双前缀。
 * fixture: e2e/fixtures/avatar-sample.mp4（640×480/3s，MP4，过反推预检 1–60s）。需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
 */
const VIDEO_FIXTURE = "e2e/fixtures/avatar-sample.mp4";

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
  return { errors: () => errors, doublePrefix: () => doublePrefix };
}

test("提示词反推·视频：切视频→上传→计费门→轮询→视频分析+提示词→带入；图片零回归；移动端可见", async ({ page }) => {
  const g = await login(page);

  // 进「提示词反推」模式。
  await page.getByRole("button", { name: "提示词反推" }).click();
  // 切「视频」来源（在「反推来源」组内，避开「视频生成」模式 chip）。
  await page.getByRole("group", { name: "反推来源" }).getByRole("button", { name: /视频/ }).first().click();
  await expect(page.getByText("提示词反推 · 视频")).toBeVisible();

  // 上传 MP4（过预检 1–60s）→ 已上传，可反推。
  // WORKBENCH-KEEPALIVE-UI-0001：面板常驻后其它（隐藏）表单的 file input 仍在 DOM → 选择器限定到当前面板。
  await page.getByTestId("panel-reverse_prompt").locator('input[type="file"]').setInputFiles(VIDEO_FIXTURE);
  await expect(page.getByText("已上传，可反推")).toBeVisible({ timeout: 20_000 });

  // 计费门：反推 → 确认弹窗(100 积分) → 确认扣费反推。
  await page.getByRole("button", { name: "反推视频提示词" }).click();
  await expect(page.getByText(/一次性扣除 100 积分/)).toBeVisible();
  await page.getByRole("button", { name: "确认扣费反推" }).click();

  // 202 + 轮询 → 结果：视频分析（video_analysis）在上 + Seedance 提示词。
  await expect(page.getByText("视频分析")).toBeVisible({ timeout: 25_000 });
  await expect(page.getByText("分镜列表")).toBeVisible();
  await expect(page.getByText(/浅景深特写|保温杯/).first()).toBeVisible();
  // 视频结果隐藏「重新反推」（防二次扣费）。
  await expect(page.getByRole("button", { name: "重新反推" })).toHaveCount(0);

  // 「带入·数字人口播」→ 切数字人口播 + 预填 topic（video fill_targets 沿用）。
  await page.getByRole("button", { name: "带入 · 数字人口播" }).click();
  await page.getByRole("button", { name: "确认带入" }).click(); // REVERSE-DEEP-UI-0001 · D3-④ 带入前确认
  await expect(page.locator("#video-topic")).toHaveValue("便携保温杯种草", { timeout: 15_000 });

  // WORKBENCH-KEEPALIVE-UI-0001：切回提示词反推 → 面板常驻，**来源选择（视频）被保留**
  // （改造前 remount 会把来源重置回图片；保留才是「切 tab 不丢」想要的）。
  await page.getByRole("button", { name: "提示词反推" }).click();
  await expect(page.getByText("提示词反推 · 视频")).toBeVisible();
  // 图片模式零回归：手动切回图片来源仍正常。
  await page.getByRole("group", { name: "反推来源" }).getByRole("button", { name: /图片/ }).first().click();
  await expect(page.getByText("提示词反推 · 图片")).toBeVisible();
  // 移动端（375）：来源二选一仍可见。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByText("反推来源")).toBeVisible();
  await expect(page.getByRole("group", { name: "反推来源" }).getByRole("button", { name: /图片/ }).first()).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
