import { expect, test } from "@playwright/test";

/**
 * ECOM-FIXES-0001 ③ 交互冒烟（根本堵漏）：现有生产构建冒烟只预渲染初始态、没点进历史，才漏了这次电商历史 #130。
 * 本用例真点「电商视频历史」tab（mock 已 seed 全状态 seedance 项 → 渲染 TaskCard 各分支），**主门禁**：
 *   - 无运行时 #130 / client-side exception —— 生产 tree-shaken 的 undefined 组件白屏（vitest 测不出）。
 *     已反证：在 TaskCard 注入 undefined 组件后本用例即失败。
 *
 * 另附一条**防御性**断言：无 /api/api 双前缀请求。⚠️ 注意其局限——Bug B（SSE /api/api）的**真正承重是
 * client.test.ts 的 base=/api 单测**：本冒烟 CI mock 环境 base=localhost（不以 /api 结尾），且电商历史视图只走
 * 列表查询、不发 SSE，故该断言在 CI 结构性恒空集；仅作「未来若 base=/api 又引入双前缀」的兜底，非 Bug B 的主门禁。
 * 需以 NEXT_PUBLIC_USE_MOCK=1 构建后 next start 运行（webServer 已配）。
 */
test("电商视频历史真点渲染无 #130 白屏（运行时 undefined 组件）", async ({ page }) => {
  const pageErrors: string[] = [];
  const react130: string[] = [];
  const doublePrefix: string[] = [];

  // 未捕获异常（React #130 渲染错误会以此冒泡 → 白屏）。
  page.on("pageerror", (err) => pageErrors.push(String(err?.message ?? err)));
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Minified React error #130|error #130|client-side exception/.test(t)) react130.push(t);
  });
  page.on("request", (req) => {
    if (req.url().includes("/api/api")) doublePrefix.push(`${req.method()} ${req.url()}`);
  });

  await page.goto("/");

  // 等 MSW service worker 接管页面（冷启动）后再登录，否则登录 POST 会逃逸到真后端。就绪失败即此处明确超时。
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });

  // 未登录被 auth-gate（(app)/layout）重定向到 /login；在该页登录（mock 接受任意凭据），成功后回到工作台 /。
  if (await page.getByRole("button", { name: "登录" }).isVisible().catch(() => false)) {
    const inputs = page.locator("form input");
    await inputs.nth(0).fill("huading");
    await inputs.nth(1).fill("qa@huading.test");
    await inputs.nth(2).fill("pw123456");
    await page.getByRole("button", { name: "登录" }).click();
    // fail-fast：登录成功即回到 /；失败在此明确超时（而非下游「找不到 tab」的语义无关超时，难诊断）。
    await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
  }

  // 真点「电商视频历史」tab → 渲染 seedance 历史项（TaskCard 各状态分支）。
  await page.getByRole("tab", { name: "电商视频历史" }).click();

  // 历史正常渲染 seed 各状态项：若某状态分支渲染 undefined 组件白屏，错误边界会使这些可见性断言失败。
  await expect(page.getByText("保温杯带货")).toBeVisible(); // done
  await expect(page.getByText("台灯带货")).toBeVisible(); // failed
  // ⚠️ cancelled 是电商历史 #130 白屏真因（批量退分产生；曾漏 seed）——门必须覆盖：cancelled 项渲染「已取消」不白屏。
  await expect(page.getByText("手电筒带货")).toBeVisible(); // cancelled
  await expect(page.getByText("已取消")).toBeVisible();

  // VIDEO-ERR-MAP-UI：失败项(error_code=VIDEO_TIMEOUT) → 友好中文映射，且**不露裸 error_message**（英文/技术串）。
  await expect(page.getByText("生成超时，请稍后重试")).toBeVisible();
  await expect(page.getByText(/Error code: 504/)).toHaveCount(0);

  // 主门禁：无 #130 白屏。
  expect(pageErrors, `page errors（含 React #130 白屏）：\n${pageErrors.join("\n")}`).toEqual([]);
  expect(react130, `#130 console：\n${react130.join("\n")}`).toEqual([]);
  // 防御性（非 Bug B 主承重，见文件头注）：无 /api/api 双前缀。
  expect(doublePrefix, `/api/api 双前缀请求：\n${doublePrefix.join("\n")}`).toEqual([]);
});
