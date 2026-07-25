import { expect, test, type Page, type Request } from "@playwright/test";

/**
 * GEN-HEARTBEAT-UI-0001 交互冒烟（生产 mock 构建 next start，真走 MSW SSE）。
 *
 * 场景 = 本包存在的理由：**推进到 10% 之后就只有心跳、百分比一动不动**（mock 心跳演示态）。
 * 单测（jsdom）能证明状态机对，但证明不了"用户真的看得见这行字、且它真的在走"——这条 e2e 补的是那半截。
 *  ① 「仍在生成（已 X 秒）」真的出现在卡片上；
 *  ② 它**真的在推进**（隔 ~2s 再看，秒数变了）——不是恒定串；
 *  ③ **百分比全程冻结在 10%**：不许假进度条、不许自己爬（本包红线）。
 * 主门禁沿用全库 e2e 惯例：无 pageerror / 无 #130 / 无「真」console.error / 无 api 失败 / 无 api 4xx-5xx / 无双前缀。
 */
async function login(page: Page): Promise<{
  realConsoleErrors: () => string[];
  pageErrors: () => string[];
  apiFailures: () => string[];
  apiHttpErrors: () => string[];
  doublePrefix: () => string[];
}> {
  const realConsoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const apiFailures: string[] = [];
  const apiHttpErrors: string[] = [];
  const doublePrefix: string[] = [];
  const isApi = (url: string) => url.includes("/api/");
  const isSse = (url: string) => url.includes("/events");

  page.on("pageerror", (err) => pageErrors.push(String(err?.message ?? err)));
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Minified React error #130|error #130|client-side exception/.test(t)) pageErrors.push(t);
    if (msg.type() === "error" && !/Failed to load resource|net::ERR_/i.test(t)) realConsoleErrors.push(t);
  });
  page.on("request", (req) => {
    if (req.url().includes("/api/api")) doublePrefix.push(`${req.method()} ${req.url()}`);
  });
  page.on("requestfailed", (req) => {
    // SSE 在页面关闭/导航时被中止是常态，不计入失败。
    if (isApi(req.url()) && !isSse(req.url())) apiFailures.push(`${req.method()} ${req.url()} — ${req.failure()?.errorText ?? ""}`);
  });
  page.on("response", (resp) => {
    if (isApi(resp.url()) && resp.status() >= 400) apiHttpErrors.push(`${resp.request().method()} ${resp.url()} — HTTP ${resp.status()}`);
  });

  // 心跳演示态必须在 MSW 起来之前就位 → addInitScript（早于页面脚本执行）。
  await page.addInitScript(() => window.localStorage.setItem("hd_mock_heartbeat", "1"));

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
  return {
    realConsoleErrors: () => realConsoleErrors,
    pageErrors: () => pageErrors,
    apiFailures: () => apiFailures,
    apiHttpErrors: () => apiHttpErrors,
    doublePrefix: () => doublePrefix
  };
}

test("生成心跳：只有心跳时显示真实计时且计时在走；百分比全程冻结（Console 0）", async ({ page }) => {
  const g = await login(page);

  // 走「视频生成」提交一单——**只因为它是最短的提交路径**（填提示词即可），
  // 与"BE 哪条链路真发心跳"无关（那由后端决定，联调时核；mock 心跳开关默认关，三条既有链路零回归）。
  await page.getByRole("button", { name: /视频生成/ }).click();
  const vg = page.getByTestId("panel-video_gen");
  await vg.getByPlaceholder(/描述你想要的画面/).fill("赛博城市夜景，霓虹运镜");
  const generate = vg.getByRole("button", { name: /生成/ });
  await expect(generate).toBeEnabled();
  await generate.click();
  await expect(page.getByRole("heading", { name: "确定生成" })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "确定", exact: true }).click();

  // ① 诚实计时出现（卡片上真的有这行字）。
  const elapsed = page.getByText(/仍在生成（已 .+）/).first();
  await expect(elapsed).toBeVisible({ timeout: 30_000 });
  const first = (await elapsed.textContent()) ?? "";

  // ② 它真的在走：等 ~2.5s 后文案必须变（恒定串 = 假计时，这里就会红）。
  await expect
    .poll(async () => (await elapsed.textContent()) ?? "", { timeout: 15_000, message: "计时文案应随时间推进" })
    .not.toBe(first);

  // ③ 百分比全程冻结在 10%：心跳绝不推进百分比（假进度红线）。
  await expect(page.getByText(/生成中 10%|撰写文案 10%/).first()).toBeVisible();
  const body = (await page.locator("body").textContent()) ?? "";
  expect(body).not.toContain("NaN");
  expect(body).not.toContain("Invalid Date");

  expect(g.pageErrors(), `page errors：\n${g.pageErrors().join("\n")}`).toEqual([]);
  expect(g.apiFailures(), `/api 网络失败：\n${g.apiFailures().join("\n")}`).toEqual([]);
  expect(g.apiHttpErrors(), `/api HTTP 4xx/5xx：\n${g.apiHttpErrors().join("\n")}`).toEqual([]);
  expect(g.realConsoleErrors(), `真 console 错误：\n${g.realConsoleErrors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
