import { expect, test, type Page } from "@playwright/test";

/**
 * REVERSE-PROMPT-UI 交互冒烟（承 ECOM-FIXES-0001「根本堵漏」；FIX1 对齐 BE 真契约后加电商图档断言）：
 * 生产构建(next start)下真走全链——切 tab → 真上传图片(拿 source_asset_id) → 反推(请求体仅 source_asset_id)
 * → 结果块 + 近似重建红线 → 点「带入」→ **断言目标表单被预填**。两条端到端锁死带入落点（page 缓冲 + 目标
 * 表单惰性消费），也堵运行时 #130（vitest 测不出的 prod tree-shaken undefined 组件白屏）。
 * 需以 NEXT_PUBLIC_USE_MOCK=1 构建后 next start 运行（webServer 已配）。
 */

async function gotoReverseResult(
  page: Page
): Promise<{ errors: () => string[]; doublePrefix: () => string[]; consoleErrors: () => string[] }> {
  const pageErrors: string[] = [];
  const doublePrefix: string[] = [];
  // REVERSE-DEEP-UI-0001：补一道**真 Console 0 error 门**（原先只收 pageerror 与 #130 白屏特征串）。
  // 新增的带入确认弹窗会在这条链上渲染，React 的 key/受控组件/a11y 类问题多半只以 console.error 现身，
  // 不抛 pageerror —— 不收就等于没测。过滤资源加载噪音（mock 环境里的占位 URL 必然 net::ERR）。
  const consoleErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err?.message ?? err)));
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Minified React error #130|error #130|client-side exception/.test(t)) pageErrors.push(t);
    if (msg.type() === "error" && !/Failed to load resource|net::ERR_/i.test(t)) consoleErrors.push(t);
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

  await page.getByRole("button", { name: "提示词反推" }).click();
  // WORKBENCH-KEEPALIVE-UI-0001：面板改为常驻后，来过的其它表单（如口播的 #avatar-image）仍留在 DOM，
  // 全局 input[type=file] 会命中多个 → 选择器必须限定到当前面板。
  await page.getByTestId("panel-reverse_prompt").locator('input[type="file"]').setInputFiles({
    name: "product.png",
    mimeType: "image/png",
    buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
  });
  const analyze = page.getByRole("button", { name: "开始反推" });
  await expect(analyze).toBeEnabled({ timeout: 15_000 });
  await analyze.click();
  // 结果块 + 近似重建红线（BE 下发 disclaimer）。
  await expect(page.getByText("不保证完全复刻原素材").first()).toBeVisible({ timeout: 15_000 });
  // REVERSE-DEEP-UI-0001 · 范围4：BE 给了 structured_prompt → 主提示词展示的是**结构化中文版**
  //（分行标注：主体/场景/构图/…），不再是逗号糊成一行的 prompt_zh。
  await expect(page.getByText("结构化提示词（中文）")).toBeVisible();
  // 🔴 FIX2 真联调订正断言形态：BE 的 `structured_prompt()`（services/reverse_prompt.py:792-806）
  //    对 en/zh 用的是**同一组 value**、只换标签，分隔符是 **ASCII 冒号 + 一个空格**：
  //      zh = "\n".join(f"{中文标签}: {value}")
  //    所以真机是「中文标签 + 与英文块相同的正文」，**不是**「全角冒号 + 中文译文」。
  //    上一版这条断言 `/主体：.*保温杯/`（全角冒号 + 中文正文）钉的是 BE 产不出的形状，
  //    只因当时 mock 也被写成了那样才绿 —— mock 改回真形状后它立刻红，正说明这条断言此前是假绿。
  await expect(page.getByText(/主体: .*insulated/)).toBeVisible();

  return { errors: () => pageErrors, doublePrefix: () => doublePrefix, consoleErrors: () => consoleErrors };
}

test("带入·数字人口播 → 预填 topic + script，无 #130 白屏", async ({ page }) => {
  const g = await gotoReverseResult(page);

  // 5 键「带入」齐备且可点（营销海报已下线 ECOM-REPLICATE-UI-0001，无该按钮）。
  for (const name of ["带入 · 数字人口播", "带入 · 电商带货", "带入 · 视频生成", "带入 · 图片生成", "带入 · AI 模特"]) {
    await expect(page.getByRole("button", { name })).toBeEnabled();
  }
  // 海报入口彻底移除 → 无「带入 · 营销海报」按钮
  await expect(page.getByRole("button", { name: "带入 · 营销海报" })).toHaveCount(0);

  await page.getByRole("button", { name: "带入 · 数字人口播" }).click();
  // REVERSE-DEEP-UI-0001 · D3-④：先弹「带入前确认」窗（默认全勾、可逐项取消/编辑）→ 确认后才落值。
  await page.getByRole("button", { name: "确认带入" }).click();
  // avatar_talk 落点：topic→#video-topic、script→#video-script（mock fill_targets.avatar_talk）。
  await expect(page.locator("#video-topic")).toHaveValue("便携保温杯种草", { timeout: 15_000 });
  await expect(page.locator("#video-script")).toHaveValue(/大家好，今天给大家安利这款便携保温杯/);

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
  // Console 0：带入确认弹窗也在这条链上渲染过，React 受控/key/a11y 类问题只会以 console.error 现身。
  expect(g.consoleErrors(), `真 console 错误：\n${g.consoleErrors().join("\n")}`).toEqual([]);
});

test("带入·AI 模特 → 切电商图·AI 模特子工具并预填自定义补充（电商图档落点）", async ({ page }) => {
  const g = await gotoReverseResult(page);

  // ecom_model 落点：切到电商图 mode + AI 模特子工具，extra_prompt→#ecom-model-custom（mock ecom_model.extra_prompt）。
  await page.getByRole("button", { name: "带入 · AI 模特" }).click();
  await page.getByRole("button", { name: "确认带入" }).click(); // D3-④ 带入前确认
  // ECOM-SUBTOOL-KEEPALIVE-UI-0001：子工具改为常驻后，#ecom-model-custom 在隐藏态也留在 DOM，而 toHaveValue
  // **不校验可见性** → 单靠它已不能证明「确实切到了 AI 模特子工具」。补一条可见性断言把落点锁死。
  const custom = page.getByTestId("panel-ecom_image").locator("#ecom-model-custom");
  await expect(custom).toBeVisible({ timeout: 15_000 });
  // 🔴 FIX2 真联调订正期望值：BE 的 `ecom_model.extra_prompt` 是
  //    `_whole_sections_within_limit(structured_en, 20000)`（services/reverse_prompt.py:779-782）——
  //    未触顶时**逐字等于 structured_prompt.en**（BE 自测 tests:2710 以整字典相等钉死），
  //    不是一句自编的中文短句。上一版期望值是 mock 自己编的，BE 从不产出那种形态。
  await expect(custom).toHaveValue(/^Subject: .*\nStyle: product advertising/s);

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
  // Console 0：带入确认弹窗也在这条链上渲染过，React 受控/key/a11y 类问题只会以 console.error 现身。
  expect(g.consoleErrors(), `真 console 错误：\n${g.consoleErrors().join("\n")}`).toEqual([]);
});
