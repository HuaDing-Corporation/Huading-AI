import { expect, test, type Page, type Request } from "@playwright/test";

/**
 * ECOM-VIDEO-OPTIMIZE-UI-0001 电商带货视频优化 交互冒烟（生产构建 next start，真走 MSW 新契约）：
 *  ① 三个新控件渲染：产品图「张数选择器」、文案「字数档位」、「负面提示词」框（多图 picker 替代单图）。
 *  ② req1/决策2：主题去必填 —— 主题留空、仅上传 1 张产品图即可「生成视频」（新下限=产品图≥1 + 音色）。
 *  ③ req7/§4.2：无产品图时「AI 生成画面」禁用 + 提示；上传后可点 → scene_prompt + negative_prompt 各自自动填入。
 *  ④ 端到端提交：主题空 + 产品图 → 确认扣费(必调 /videos/estimate) → 202（防假绿：mock 校验 product_image_keys≥1，接线断即报错）。
 * 主门禁（对齐全库 e2e 惯例，抓真错误不被环境噪音绊）：无 pageerror、无 #130 白屏、无「真」console.error、
 * 无 api 网络失败(ERR_FAILED)、无 api HTTP 4xx/5xx、无 api 双前缀。FIX2 硬化：① 除网络失败外增 HTTP 4xx/5xx 门
 * （estimate/submit 返 500 时请求"成功"但语义失败，也红）；② 最终断言前 waitApiIdle 等被跟踪的 /api 请求收敛
 * （消时序假绿——本地曾因失败请求未结束就断言而假绿）。注意：不断言「任何 error 级 console」——mock 生产构建下浏览器
 * 对未被 MSW 拦截的远程静态资源（seed 图片/音频等 mock.local、cdn 地址在无真后端的 CI 沙箱无法解析）报 net::ERR_FAILED，
 * 是全库 e2e 共有的环境噪音、非本功能缺陷（FIX1 缘起：原「所有 error 级」断言被这些 ERR_FAILED 绊红）。
 * 需以 NEXT_PUBLIC_USE_MOCK=1 构建后 next start 运行。
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
  const isSse = (url: string) => url.includes("/events"); // SSE 长连接永不完成，不纳入在途等待

  page.on("pageerror", (err) => pageErrors.push(String(err?.message ?? err))); // 未捕获 JS 异常（含 #130 白屏）
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Minified React error #130|error #130|client-side exception/.test(t)) pageErrors.push(t);
    // 真 console.error 才计入 Console 门。滤掉「资源加载失败」：mock 生产构建下浏览器对未被 MSW 拦截的远程静态资源
    // （seed 数据里的图片/音频等 mock.local/cdn 地址在无真后端的 CI 沙箱无法解析）报 net::ERR_FAILED，是全库 e2e 共有的
    // 环境噪音、非本功能缺陷；真 API 断线另由 apiFailures / apiHttpErrors 精确抓（详见文件头）。
    if (msg.type() === "error" && !/Failed to load resource|net::ERR_/i.test(t)) realConsoleErrors.push(t);
  });
  page.on("request", (req) => {
    const url = req.url();
    if (url.includes("/api/api")) doublePrefix.push(`${req.method()} ${url}`);
    if (isApi(url) && !isSse(url)) inflightApi.add(req); // 跟踪短 /api 请求（estimate/submit/list…），SSE 除外
  });
  page.on("requestfinished", (req) => inflightApi.delete(req));
  page.on("requestfailed", (req) => {
    const url = req.url();
    // ① 网络级失败（ERR_FAILED，如缺 mock 放行到真后端）：只关切 /api（远程静态资源失败是 mock 环境噪音，见文件头）。
    if (isApi(url)) apiFailures.push(`${req.method()} ${url} — ${req.failure()?.errorText ?? ""}`);
    inflightApi.delete(req);
  });
  page.on("response", (resp) => {
    // ② HTTP 4xx/5xx（P2 硬化）：只抓网络失败不够——estimate/submit 返 500 时请求"成功"但语义失败，也须红，防假绿。
    if (isApi(resp.url()) && resp.status() >= 400) {
      apiHttpErrors.push(`${resp.request().method()} ${resp.url()} — HTTP ${resp.status()}`);
    }
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

  // 最终断言前等所有被跟踪的 /api 请求收敛（P2 消时序假绿：本地曾因 estimate 失败请求尚未结束就断言而假绿）。
  const waitApiIdle = async () => {
    await expect.poll(() => inflightApi.size, { timeout: 10_000, message: "等待在途 /api 请求收敛" }).toBe(0);
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

test("电商带货视频优化：新控件 + 主题可空提交 + AI生成画面门禁与自动填负面（Console 0）", async ({ page }) => {
  const g = await login(page);

  await page.getByRole("button", { name: "电商带货" }).click();
  const ecom = page.getByTestId("panel-seedance_i2v");

  // ① 三个新控件渲染（KEEPALIVE：限定到电商面板 scope）。
  await expect(ecom.getByText("产品图张数")).toBeVisible();
  await expect(ecom.getByText("文案长度")).toBeVisible();
  await expect(ecom.getByText("负面提示词（可选）")).toBeVisible();
  await expect(ecom.getByRole("button", { name: /上传产品图/ })).toBeVisible();

  // ③ 无产品图 → 「AI 生成画面」禁用 + 提示。
  const sceneBtn = ecom.getByRole("button", { name: "AI 生成画面" });
  await expect(sceneBtn).toBeDisabled();
  await expect(ecom.getByText("请先上传产品图，再生成画面")).toBeVisible();

  // 生成视频：无产品图时禁用 + 「请上传产品图」提示（主题留空亦不再拦）。
  const generate = ecom.getByRole("button", { name: /生成视频/ });
  await expect(generate).toBeDisabled();
  await expect(ecom.getByText("请上传产品图后再生成")).toBeVisible();

  // 上传 1 张产品图（主题留空）。
  await ecom.locator('input[type="file"]#product-image').setInputFiles(PNG);
  await expect(ecom.getByRole("button", { name: /上传产品图（1\/1）/ })).toBeVisible({ timeout: 15_000 });

  // ② 主题空 + 仅产品图 → 可生成。
  await expect(generate).toBeEnabled();

  // ③ 上传后「AI 生成画面」可点 → scene_prompt + negative_prompt 各自自动填入（MSW 新契约）。
  await expect(sceneBtn).toBeEnabled();
  await sceneBtn.click();
  await expect(ecom.locator("#scene-prompt")).toHaveValue(/白色大理石台面/, { timeout: 15_000 });
  await expect(ecom.locator("#ecom-negative-prompt")).toHaveValue(/水印/);

  // 字数档位可切（长）。
  await ecom.getByRole("button", { name: "长", exact: true }).click();
  await expect(ecom.getByRole("button", { name: "长", exact: true })).toHaveAttribute("aria-pressed", "true");

  // ④ 端到端提交：确认扣费 → 无错（mock 校验 product_image_keys≥1，接线断即 422 报错）。
  await generate.click();
  await expect(page.getByRole("heading", { name: "确定生成" })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "确定", exact: true }).click();
  // 提交成功后确认窗关闭、无错误提示（seedance_i2v + product_image_keys 被 mock 接受）。
  await expect(page.getByRole("heading", { name: "确定生成" })).toHaveCount(0, { timeout: 15_000 });

  await g.waitApiIdle(); // 先等在途 /api 收敛，再断言（消时序假绿）
  expect(g.pageErrors(), `page errors（含 #130 白屏）：\n${g.pageErrors().join("\n")}`).toEqual([]);
  expect(g.apiFailures(), `/api 网络失败（ERR_FAILED，如缺 mock 放行）：\n${g.apiFailures().join("\n")}`).toEqual([]);
  expect(g.apiHttpErrors(), `/api HTTP 4xx/5xx：\n${g.apiHttpErrors().join("\n")}`).toEqual([]);
  expect(g.realConsoleErrors(), `真 console 错误（已滤远程资源加载噪音）：\n${g.realConsoleErrors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
