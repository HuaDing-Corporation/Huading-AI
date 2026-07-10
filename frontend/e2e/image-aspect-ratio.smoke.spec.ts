import { expect, test, type Page } from "@playwright/test";

/**
 * IMAGE-ASPECT-RATIO-UI-0001 交互冒烟（生产构建 next start）：图片生成去「质量」+ 加「画面比例」——
 * 选 16:9 → 提交 POST /videos 带 aspect_ratio:"16:9"、无 image_quality；自适应 → 语义 hint；电商图·白底图 亦有画面比例；
 * 移动端(375) 选择器可见。全程无 #130 / 无 /api/api 双前缀。需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
 */
async function login(page: Page): Promise<{
  errors: () => string[];
  doublePrefix: () => string[];
  lastVideoBody: () => Record<string, unknown> | null;
}> {
  const errors: string[] = [];
  const doublePrefix: string[] = [];
  let videoBody: Record<string, unknown> | null = null;
  page.on("pageerror", (err) => errors.push(String(err?.message ?? err)));
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Minified React error #130|error #130|client-side exception/.test(t)) errors.push(t);
  });
  page.on("request", (req) => {
    if (req.url().includes("/api/api")) doublePrefix.push(`${req.method()} ${req.url()}`);
    if (req.method() === "POST" && new URL(req.url()).pathname.endsWith("/api/v1/videos")) {
      try {
        videoBody = req.postDataJSON() as Record<string, unknown>;
      } catch {
        /* ignore */
      }
    }
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
  return { errors: () => errors, doublePrefix: () => doublePrefix, lastVideoBody: () => videoBody };
}

test("图片生成：去质量 + 画面比例(选 16:9 提交) + 自适应提示；电商白底图有画面比例；移动端可见", async ({ page }) => {
  const g = await login(page);

  // 进「图片生成 / 修改」。
  await page.getByRole("button", { name: "图片生成" }).click();
  // 去「质量」下拉；有「画面比例」。
  await expect(page.getByText("质量", { exact: true })).toHaveCount(0);
  await expect(page.getByText("画面比例")).toBeVisible();

  // 选 16:9。
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: "16:9", exact: true }).click();

  // 生成 → 确认 → 提交体带 aspect_ratio:16:9、无 image_quality。
  await page.getByPlaceholder(/描述想要的图片/).fill("赛博城市夜景");
  await page.getByRole("button", { name: /生成图片/ }).click();
  await page.getByRole("button", { name: "确定" }).click();
  await expect.poll(() => g.lastVideoBody()?.aspect_ratio, { timeout: 15_000 }).toBe("16:9");
  expect(g.lastVideoBody()).not.toHaveProperty("image_quality");

  // 自适应：选 auto → 语义 hint 显示。
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: "自适应" }).click();
  await expect(page.getByText(/自适应：有输入图/)).toBeVisible();

  // 电商图 · 白底图：亦有画面比例。
  await page.getByRole("button", { name: "电商图", exact: true }).click();
  await expect(page.getByText("画面比例")).toBeVisible();

  // 移动端（375）：画面比例选择器仍可见。
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByText("画面比例")).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
