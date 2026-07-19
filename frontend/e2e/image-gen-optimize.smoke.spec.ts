import { expect, test, type Page, type Request } from "@playwright/test";

/**
 * IMAGE-GEN-OPTIMIZE-UI-0001 图片生成/修改优化 交互冒烟（生产构建 next start，真走 MSW 新契约）：
 *  ① 新控件渲染：参考图「张数选择器」+ 多图 picker（替代单图）、三个「强度滑块」组（可折叠；背景参考强度已砍除）、四层提示词（图片负面 + 任务总控组）。
 *  ② req1：参考图多图 —— 上传 1 张 → 提交体带 image_keys（非标量 image_key）。
 *  ③ req2：开启「图片相似度」强度 → slider 启用 → 提交体带 similarity_strength（默认关的其余强度不出现）。
 *  ④ 端到端提交 → 202（mock 校验 image_keys 1–6 / 强度 10..100 步10，接线断即报错）。
 * 主门禁（对齐全库 e2e 惯例）：无 pageerror、无 #130、无「真」console.error、无 api 网络失败、无 api HTTP 4xx/5xx、
 * 无 api 双前缀。不断言「任何 error 级 console」——mock 生产构建下未被 MSW 拦截的远程静态资源报 net::ERR_FAILED 是全库
 * e2e 共有的环境噪音（详见 ecom-video-optimize.smoke.spec 文件头）。需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
 */
const PNG = { name: "r.png", mimeType: "image/png", buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]) };

async function login(page: Page): Promise<{
  realConsoleErrors: () => string[];
  pageErrors: () => string[];
  apiFailures: () => string[];
  apiHttpErrors: () => string[];
  doublePrefix: () => string[];
  lastVideoBody: () => Record<string, unknown> | null;
  waitApiIdle: () => Promise<void>;
}> {
  const realConsoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const apiFailures: string[] = [];
  const apiHttpErrors: string[] = [];
  const doublePrefix: string[] = [];
  let videoBody: Record<string, unknown> | null = null;
  const inflightApi = new Set<Request>();
  const isApi = (url: string) => url.includes("/api/");
  const isSse = (url: string) => url.includes("/events");

  page.on("pageerror", (err) => pageErrors.push(String(err?.message ?? err)));
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Minified React error #130|error #130|client-side exception/.test(t)) pageErrors.push(t);
    if (msg.type() === "error" && !/Failed to load resource|net::ERR_/i.test(t)) realConsoleErrors.push(t);
  });
  page.on("request", (req) => {
    const url = req.url();
    if (url.includes("/api/api")) doublePrefix.push(`${req.method()} ${url}`);
    if (isApi(url) && !isSse(url)) inflightApi.add(req);
    if (req.method() === "POST" && new URL(url).pathname.endsWith("/api/v1/videos")) {
      try {
        videoBody = req.postDataJSON() as Record<string, unknown>;
      } catch {
        /* ignore */
      }
    }
  });
  page.on("requestfinished", (req) => inflightApi.delete(req));
  page.on("requestfailed", (req) => {
    if (isApi(req.url())) apiFailures.push(`${req.method()} ${req.url()} — ${req.failure()?.errorText ?? ""}`);
    inflightApi.delete(req);
  });
  page.on("response", (resp) => {
    if (isApi(resp.url()) && resp.status() >= 400) apiHttpErrors.push(`${resp.request().method()} ${resp.url()} — HTTP ${resp.status()}`);
  });

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

  const waitApiIdle = async () => {
    await expect.poll(() => inflightApi.size, { timeout: 10_000, message: "等待在途 /api 请求收敛" }).toBe(0);
  };
  return {
    realConsoleErrors: () => realConsoleErrors,
    pageErrors: () => pageErrors,
    apiFailures: () => apiFailures,
    apiHttpErrors: () => apiHttpErrors,
    doublePrefix: () => doublePrefix,
    lastVideoBody: () => videoBody,
    waitApiIdle
  };
}

test("图片生成优化：新控件 + 多图参考(image_keys) + 强度端到端(similarity_strength) 提交（Console 0）", async ({ page }) => {
  const g = await login(page);

  await page.getByRole("button", { name: "图片生成" }).click();
  const photo = page.getByTestId("panel-photo");

  // ① 新控件渲染（限定到 photo 面板 scope）。
  await expect(photo.getByText("参考图张数")).toBeVisible();
  await expect(photo.getByRole("button", { name: /上传参考图/ })).toBeVisible();
  await expect(photo.getByText("图片负面提示词（可选）")).toBeVisible();
  await expect(photo.getByText("生成强度（可选）")).toBeVisible();
  await expect(photo.getByText("任务总控（可选 · 全局风格）")).toBeVisible();
  await expect(photo.getByText("清晰度档位")).toBeVisible(); // §3之二
  await expect(photo.getByRole("button", { name: "1K" })).toHaveAttribute("aria-pressed", "true"); // 默认 1K

  // 图片提示词（必填）。
  await photo.getByPlaceholder(/描述想要的图片/).fill("白色大理石台面上的香水瓶");

  // ② 上传 1 张参考图。
  await photo.locator('input[type="file"]#photo-ref').setInputFiles(PNG);
  await expect(photo.getByRole("button", { name: /上传参考图（1\/1）/ })).toBeVisible({ timeout: 15_000 });

  // ③ 展开生成强度 → 开「图片相似度」→ slider 启用（关闭时 disabled）。
  await photo.getByText("生成强度（可选）").click();
  const simSlider = photo.getByRole("slider", { name: "图片相似度" }); // role=slider 消歧（开关 aria-label 含同名子串）
  await expect(simSlider).toBeDisabled(); // 默认关
  await photo.getByRole("switch", { name: "图片相似度开关" }).click();
  await expect(simSlider).toBeEnabled();

  // ④ 端到端提交 → 提交体带 image_keys + similarity_strength（默认关的其余强度不出现）。
  const generate = photo.getByRole("button", { name: /生成图片/ });
  await expect(generate).toBeEnabled();
  await generate.click();
  await expect(page.getByRole("heading", { name: "确定生成" })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "确定", exact: true }).click();
  await expect(page.getByRole("heading", { name: "确定生成" })).toHaveCount(0, { timeout: 15_000 });

  await g.waitApiIdle();
  const body = g.lastVideoBody();
  expect(Array.isArray(body?.image_keys)).toBeTruthy();
  expect((body?.image_keys as string[]).length).toBe(1);
  expect(typeof body?.similarity_strength).toBe("number");
  expect(body).not.toHaveProperty("creativity_strength"); // 未开启的强度不出现
  expect(body).not.toHaveProperty("image_key");
  expect(body?.image_resolution).toBe("1k"); // §3之二：清晰度档位总随请求传（默认 1k）

  expect(g.pageErrors(), `page errors（含 #130）：\n${g.pageErrors().join("\n")}`).toEqual([]);
  expect(g.apiFailures(), `/api 网络失败：\n${g.apiFailures().join("\n")}`).toEqual([]);
  expect(g.apiHttpErrors(), `/api HTTP 4xx/5xx：\n${g.apiHttpErrors().join("\n")}`).toEqual([]);
  expect(g.realConsoleErrors(), `真 console 错误：\n${g.realConsoleErrors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
