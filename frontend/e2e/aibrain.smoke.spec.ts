import { expect, test, type Page, type Request } from "@playwright/test";

/**
 * AIBRAIN-UI-0001 华鼎AI智脑 交互冒烟（生产构建 next start，真走 MSW 对齐 BE 增量 1 的契约）：
 *  ① 进 /aibrain：标题 + 智能强度选择器 + 推理积分余额渲染。
 *  ② 余额为 0（BE 新租户）发送 → **前端预检拦住、弹充值窗**（不发请求，故无 402 网络错误）+ 明示「单向不可退」。
 *  ③ 充值 500 → 余额更新 → 再发送 → 助手回答出现（选档随请求传、结算回填余额）。
 *  ④ 新建对话 → 切换 → 不串数据（新对话空）。
 * 主门禁（对齐全库 e2e 惯例）：无 pageerror、无 #130 白屏、无「真」console.error、无 /api 网络失败、
 * 无 /api HTTP 4xx/5xx、无 /api/api 双前缀。**本流程刻意走 happy path（先充值再发送）以不触发 402/422**
 * ——余额不足→充值弹窗由前端预检触发（零网络调用），故不会有 4xx。需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
 */
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
  // 全库共有的**永不收敛**后台轮询（非本功能）：/auth/me 心跳、seed 视频（种子任务恒 running）状态轮询。
  // 与 ecom 测试排除 SSE 同理——它们按设计一直重发，不纳入「本功能请求收敛」的等待；但仍被下面的错误门监视。
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
    // ERR_ABORTED = 客户端主动取消（SPA 换页时 react-query 中止上一页在途查询）——预期行为、非网络故障，不计入。
    if (isApi(req.url()) && !/ERR_ABORTED/.test(errText)) apiFailures.push(`${req.method()} ${req.url()} — ${errText}`);
    inflightApi.delete(req);
  });
  page.on("response", (resp) => {
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

  const waitApiIdle = async () => {
    await expect
      .poll(() => inflightApi.size, {
        timeout: 8_000,
        message: `在途 /api：${[...inflightApi].map((r) => `${r.method()} ${r.url()}`).join(" | ")}`
      })
      .toBe(0);
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

test("华鼎AI智脑：选档 → 余额不足弹充值 → 充值 → 发消息得回答 → 切会话不串数据（Console 0）", async ({ page }) => {
  const g = await login(page);
  // 客户端换页（点侧栏导航）而非硬 goto —— 保持 MSW service worker 控制，/aibrain 请求才被拦截（否则打到真后端）。
  await page.getByRole("link", { name: "华鼎AI智脑" }).click();
  await page.waitForURL("**/aibrain", { timeout: 20_000 });

  // ① 页面渲染：标题 + 智能强度 + 余额。
  await expect(page.getByRole("heading", { name: "华鼎AI智脑" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("radiogroup", { name: "智能强度" })).toBeVisible();
  await expect(page.getByText("推理积分")).toBeVisible();

  // ② 余额 0 发送 → 前端预检弹充值窗（不发请求）+ 单向不可退。
  const input = page.locator("#aibrain-composer");
  await input.fill("你好，介绍一下你自己");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByRole("heading", { name: "充值推理积分" })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/单向不可退/)).toBeVisible();

  // ③ 充值 500 → 确认 → 弹窗关闭、余额更新。
  await page.getByRole("radio", { name: "500 积分" }).click();
  await page.getByRole("button", { name: "确认充值" }).click();
  await expect(page.getByRole("heading", { name: "充值推理积分" })).toHaveCount(0, { timeout: 10_000 });

  // 选高档，再发送 → 助手回答出现（mock 回答含「已收到」）。
  await page.getByRole("radio", { name: /智能强度 高/ }).click();
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(/已收到/)).toBeVisible({ timeout: 15_000 });

  // §3 附件缩略图：上传图片（走既有 /uploads/images → asset_id）→ 发送 → 历史消息显示**真缩略图**
  // （ChatAttachmentRead.download_url）。⚠️ 真接口下 download_url 是 presign；此处 mock 环境是占位 URL，
  //   浏览器加载它会 net::ERR（已被资源加载噪音过滤器排除），断言只验「缩略图 <img> 落地 DOM」。
  const PNG = { name: "p.png", mimeType: "image/png", buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]) };
  await page.locator('input[type="file"]').setInputFiles(PNG);
  await expect(page.locator('img[src^="blob:"]')).toBeVisible({ timeout: 10_000 }); // 上传完成、组件本地预览出现
  await page.locator("#aibrain-composer").fill("看这张图");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.locator('img[src*="mock.local/aibrain"]').first()).toBeVisible({ timeout: 15_000 }); // 消息里的真缩略图

  // ④ 新建对话 → 切到新对话 → 不串数据（上一条消息不在新对话里）。
  await page.getByRole("button", { name: "新建对话" }).click();
  await expect(page.getByText(/已收到/)).toHaveCount(0, { timeout: 10_000 });
  await expect(page.getByText("开始和华鼎AI智脑对话")).toBeVisible();

  await g.waitApiIdle();
  expect(g.pageErrors(), `page errors（含 #130 白屏）：\n${g.pageErrors().join("\n")}`).toEqual([]);
  expect(g.apiFailures(), `/api 网络失败：\n${g.apiFailures().join("\n")}`).toEqual([]);
  expect(g.apiHttpErrors(), `/api HTTP 4xx/5xx：\n${g.apiHttpErrors().join("\n")}`).toEqual([]);
  expect(g.realConsoleErrors(), `真 console 错误：\n${g.realConsoleErrors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
