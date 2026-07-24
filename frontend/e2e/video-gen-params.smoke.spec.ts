import { expect, test, type Page, type Request } from "@playwright/test";

/**
 * VIDEO-GEN-PARAMS-UI-0001 视频生成参数优化 交互冒烟（生产构建 next start，真走 MSW 新契约）：
 *  ① 新控件渲染：负面提示词、画面比例(7 值/默认自适应)、音频生成开关(默认关)、时长自定义(4–15)、提示词 2000 计数。
 *  ② 2000 墙：提示词 2001 → 红字「提示词输入最大上限为 2000 字」+ 生成禁用（前端拦，不发请求）。
 *  ③ 端到端提交 → 提交体带 aspect_ratio(16:9) + generate_audio(true) + duration_sec(自定义8) + negative_prompt + 参考图 asset_id。
 * 主门禁（对齐全库 e2e 惯例）：无 pageerror、无 #130、无「真」console.error、无 api 网络失败、无 api HTTP 4xx/5xx、
 * 无 api 双前缀。不断言「任何 error 级 console」——远程静态资源 net::ERR_FAILED 是全库 e2e 共有环境噪音。需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
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

test("视频生成优化：新控件 + 2000 墙 + 画面比例/音频/自定义时长/负面 端到端提交（Console 0）", async ({ page }) => {
  const g = await login(page);

  await page.getByRole("button", { name: /视频生成/ }).click();
  const vg = page.getByTestId("panel-video_gen");

  // ① 新控件渲染（限定 vg 面板 scope）。
  await expect(vg.getByText("负面提示词（可选）")).toBeVisible();
  await expect(vg.getByText("画面比例")).toBeVisible();
  await expect(vg.getByText("音频生成", { exact: true })).toBeVisible();
  await expect(vg.getByText(/自适应：由模型/)).toBeVisible(); // 默认 adaptive 的自适应说明
  await expect(vg.getByRole("switch", { name: "音频生成开关" })).toBeVisible();
  await expect(vg.getByRole("button", { name: "自定义" })).toBeVisible(); // 时长自定义档

  // ①b V2V（VIDEO-GEN-V2V-UI-0001）：「参考图或视频（可选）」组 + 参考视频区 + D5「不能包含真人」显著明示。
  await expect(vg.getByText("参考图或视频（可选）")).toBeVisible();
  await expect(vg.getByText(/参考视频不能包含真人/)).toBeVisible();
  await expect(vg.getByRole("button", { name: /添加参考视频/ })).toBeVisible();

  // ② 上传 1 张参考图（参考图现可选；此处走图片路径）。
  await vg.locator('input[type="file"]#vg-ref-images').setInputFiles(PNG);
  await expect(vg.getByRole("button", { name: /添加参考图（1\/9）/ })).toBeVisible({ timeout: 15_000 });

  // ②b D8 严格二选一：已传参考图 → 参考视频上传禁用 + 互斥原因可见（UI 直接互斥，不落 422）。
  await expect(vg.getByRole("button", { name: /添加参考视频/ })).toBeDisabled();
  await expect(vg.getByText(/移除全部参考图后才能改传参考视频/)).toBeVisible();

  // ③ 2000 墙：2001 → 红字 + 生成禁用（前端拦）。
  const promptBox = vg.getByPlaceholder(/描述你想要的画面/);
  await promptBox.fill("x".repeat(2001));
  await expect(vg.getByText("提示词输入最大上限为 2000 字")).toBeVisible();
  await expect(vg.getByRole("button", { name: /生成/ })).toBeDisabled();

  // ④ 改回合法提示词 + 负面 + 音频开 + 画面比例 16:9 + 自定义时长 8。
  await promptBox.fill("赛博城市夜景，霓虹运镜");
  await vg.getByPlaceholder(/不希望出现的元素/).fill("水印、人物变形");
  await vg.getByRole("switch", { name: "音频生成开关" }).click();
  await vg.getByRole("combobox", { name: /画面比例/ }).click();
  await page.getByRole("option", { name: "16:9" }).click();
  await vg.getByRole("button", { name: "自定义" }).click();
  await vg.getByLabel("自定义时长（秒）").fill("8");

  // ⑤ 端到端提交 → 提交体校验。
  const generate = vg.getByRole("button", { name: /生成/ });
  await expect(generate).toBeEnabled();
  await generate.click();
  await expect(page.getByRole("heading", { name: "确定生成" })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "确定", exact: true }).click();
  await expect(page.getByRole("heading", { name: "确定生成" })).toHaveCount(0, { timeout: 15_000 });

  await g.waitApiIdle();
  const body = g.lastVideoBody();
  expect(body?.video_mode).toBe("video_gen");
  expect(body?.aspect_ratio).toBe("16:9");
  expect(body?.generate_audio).toBe(true);
  expect(body?.duration_sec).toBe(8);
  expect(body?.negative_prompt).toBe("水印、人物变形");
  expect(Array.isArray(body?.reference_image_asset_ids)).toBeTruthy();
  expect((body?.reference_image_asset_ids as string[]).length).toBe(1);

  expect(g.pageErrors(), `page errors（含 #130）：\n${g.pageErrors().join("\n")}`).toEqual([]);
  expect(g.apiFailures(), `/api 网络失败：\n${g.apiFailures().join("\n")}`).toEqual([]);
  expect(g.apiHttpErrors(), `/api HTTP 4xx/5xx：\n${g.apiHttpErrors().join("\n")}`).toEqual([]);
  expect(g.realConsoleErrors(), `真 console 错误：\n${g.realConsoleErrors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
