import { expect, test, type Page, type Request } from "@playwright/test";

/**
 * ECOM-MODEL-OPTIMIZE-UI-0001 交互冒烟（生产构建 next start，真走 MSW 对齐 §四契约）：
 *  ① 进电商图 → AI 模特：上传 2 张商品图 → 剩余额度联动（还可上传 4 张）+ 模特图上限自动变 4（D1）。
 *  ② >1 商品图 → 出现「商品图组合方式」开关，选「同一件商品的多角度」（D2）。
 *  ③ 风格：填自定义风格 → 预设禁用；清空 → 选预设（D3 互斥）。
 *  ④ 自定义补充填 250 字 → 不被截断（D4）。
 *  ⑤ 生成 → 结果模特图落地。
 * 主门禁（对齐全库 e2e）：无 pageerror / #130 白屏 / 真 console.error / /api 网络失败 / /api HTTP 4xx-5xx / /api/api 双前缀。
 * 需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
 */
const PNG = { name: "p.png", mimeType: "image/png", buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]) };

async function login(page: Page): Promise<{
  realConsoleErrors: () => string[];
  pageErrors: () => string[];
  apiFailures: () => string[];
  apiHttpErrors: () => string[];
  doublePrefix: () => string[];
  waitApiIdle: () => Promise<void>;
}> {
  const realConsoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const apiFailures: string[] = [];
  const apiHttpErrors: string[] = [];
  const doublePrefix: string[] = [];
  const inflightApi = new Set<Request>();
  const isApi = (url: string) => url.includes("/api/");
  // 全库共有的**永不收敛**后台轮询（非本功能）：/auth/me 心跳、seed 视频状态轮询、trackExisting 的产物轮询。
  const isBackgroundPoll = (url: string) => /\/auth\/me|\/api\/v1\/videos\//.test(url);

  page.on("pageerror", (err) => pageErrors.push(String(err?.message ?? err)));
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Minified React error #130|error #130|client-side exception/.test(t)) pageErrors.push(t);
    if (msg.type() === "error" && !/Failed to load resource|net::ERR_/i.test(t)) realConsoleErrors.push(t);
  });
  page.on("request", (req) => {
    const url = req.url();
    if (url.includes("/api/api")) doublePrefix.push(`${req.method()} ${url}`);
    if (isApi(url) && !isBackgroundPoll(url)) inflightApi.add(req);
  });
  page.on("requestfinished", (req) => inflightApi.delete(req));
  page.on("requestfailed", (req) => {
    const errText = req.failure()?.errorText ?? "";
    if (isApi(req.url()) && !/ERR_ABORTED/.test(errText)) apiFailures.push(`${req.method()} ${req.url()} — ${errText}`);
    inflightApi.delete(req);
  });
  page.on("response", (resp) => {
    if (isApi(resp.url()) && resp.status() >= 400) apiHttpErrors.push(`${resp.request().method()} ${resp.url()} — HTTP ${resp.status()}`);
  });

  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
  const inputs = page.locator("form input");
  await inputs.nth(0).fill("huading");
  await inputs.nth(1).fill("qa@huading.test");
  await inputs.nth(2).fill("pw123456");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });

  const waitApiIdle = async () => {
    await expect.poll(() => inflightApi.size, { timeout: 8_000 }).toBe(0);
  };
  return {
    realConsoleErrors: () => realConsoleErrors,
    pageErrors: () => pageErrors,
    apiFailures: () => apiFailures,
    apiHttpErrors: () => apiHttpErrors,
    doublePrefix: () => doublePrefix,
    waitApiIdle
  };
}

test("电商图 · AI 模特优化：额度联动 + 组合语义 + 风格互斥 + 补充不截断 + 生成（Console 0）", async ({ page }) => {
  const g = await login(page);
  await page.getByRole("button", { name: "电商图", exact: true }).click();
  await expect(page.getByTestId("subtool-cutout")).toBeVisible({ timeout: 15_000 });
  // 切 AI 模特子工具（惰性挂载 → 首次访问才拉 model-styles）。
  await page.getByRole("button", { name: "AI 模特", exact: true }).click();
  const model = page.getByTestId("subtool-model");
  await expect(model).toBeVisible({ timeout: 15_000 });

  // ① 上传 2 张商品图 → 剩余额度联动 + 模特图上限自动变 4（D1）。
  await model.locator("#ecom-model-product").setInputFiles([PNG, { ...PNG, name: "p2.png" }]);
  await expect(model.getByText("还可上传 4 张（商品图 + 模特图合计 ≤ 6）")).toBeVisible({ timeout: 15_000 });
  await expect(model.getByRole("button", { name: /上传模特图（0\/4）/ })).toBeVisible();

  // ② >1 商品图 → 组合方式开关出现，选「同一件商品的多角度」（D2）。
  await expect(model.getByRole("group", { name: "商品图组合方式" })).toBeVisible();
  await model.getByRole("button", { name: "同一件商品的多角度" }).click();
  await expect(model.getByRole("button", { name: "同一件商品的多角度" })).toHaveAttribute("aria-pressed", "true");

  // ③ 风格互斥（D3）：填自定义风格 → 预设禁用；清空 → 选预设。
  await model.locator("#ecom-model-custom-style").fill("赛博朋克霓虹夜景");
  await expect(model.getByRole("button", { name: "棚拍白底" })).toBeDisabled();
  await model.locator("#ecom-model-custom-style").fill("");
  await model.getByRole("button", { name: "街拍" }).click();
  await expect(model.getByRole("button", { name: "街拍" })).toHaveAttribute("aria-pressed", "true");

  // ④ 自定义补充填 250 字 → 不被截断（D4）。
  const long = "补".repeat(250);
  await model.locator("#ecom-model-custom").fill(long);
  await expect(model.locator("#ecom-model-custom")).toHaveValue(long);

  // ⑤ 生成 → 结果模特图落地（mock 返回 done task，trackExisting 轮询拿到产物）。
  await model.getByRole("button", { name: "生成" }).click();
  await expect(model.locator('img[src*="mock.local/model-"]').first()).toBeVisible({ timeout: 15_000 });

  await g.waitApiIdle();
  expect(g.pageErrors(), `page errors：\n${g.pageErrors().join("\n")}`).toEqual([]);
  expect(g.apiFailures(), `/api 网络失败：\n${g.apiFailures().join("\n")}`).toEqual([]);
  expect(g.apiHttpErrors(), `/api HTTP 4xx/5xx：\n${g.apiHttpErrors().join("\n")}`).toEqual([]);
  expect(g.realConsoleErrors(), `真 console 错误：\n${g.realConsoleErrors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
