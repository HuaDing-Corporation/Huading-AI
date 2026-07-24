import { expect, test, type Page } from "@playwright/test";

/**
 * HISTORY-VIDEO-REVERSE-UI-0001 · FIX1 —— 「历史反推记录 → 带入 → 目标表单真的收到值」端到端。
 *
 * 为什么必须有这条（Cowork 驳回「省 e2e」的理由，成立）：这条链跨四层，而组件测试各自 mock 掉了自己的边界，
 * **没有任何一层真的走通过它**：
 *   ReversePromptResultView(弹窗 APPLY_BUTTONS) → GenerationHistory(新 onApplyPrefill prop)
 *   → page.tsx(injectPrefill → setPendingPrefill + activate) → 目标表单(useEffect 同步消费 → clearPrefill)
 * 另三条：① #181 刚在 prefill 链抓过 P1（三重防线/父子时序/回调引用稳定性）；② 生产验收过的是**反推页**的
 * 带入，历史弹窗是**新落点**，旧的绿不能证明新的绿；③ `activate`（首次挂载目标面板）+ 同时注入 prefill 这个
 * 组合从没跑过 —— #182 那条「不入列即永久卡死」的致命前提就是同类问题。
 *
 * 落点选 **电商图 · AI 模特**（而非数字人口播）：avatar_talk 是默认 mode、**首屏就挂着** → 测不到 activate 的
 * 首次挂载路径，而那恰是本包最该验的一环。ecom_image 首屏未挂，一条用例即串起三段：
 *   page 的 activate 首挂面板 → EcomImageWorkbench 据 initialTool 切到 AI 模特子工具 → 子组件消费 initialCustom。
 *
 * 需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
 */
function watch(page: Page): { errors: () => string[]; doublePrefix: () => string[] } {
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
  return { errors: () => errors, doublePrefix: () => doublePrefix };
}

async function login(page: Page) {
  // ⚠️ 必须 /login 而非 "/"：未登录进站根已分流 /landing（LANDING-ENTRY-UI-0001，#151/#153 都在这栽过）。
  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
  const inputs = page.locator("form input");
  await inputs.nth(0).fill("huading");
  await inputs.nth(1).fill("qa@huading.test");
  await inputs.nth(2).fill("pw123456");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
}

test("历史反推记录 → 带入 · AI 模特 → 电商图面板被激活且自定义补充真的收到值", async ({ page }) => {
  const g = watch(page);
  await login(page);

  // 前提（#181 惰性挂载）：电商图面板此刻**压根不在 DOM** —— 这正是本条要验的 activate 首挂路径的起点。
  await expect(page.getByTestId("panel-ecom_image")).toHaveCount(0);

  // 进「历史生成」→ 第 6 个 tab「提示词反推历史」。
  await page.getByRole("tab", { name: "提示词反推历史" }).click();
  const panel = page.getByRole("tabpanel", { name: "提示词反推历史" });
  await expect(panel).toBeVisible({ timeout: 15_000 });

  // 二级分类（tab 内再分）：默认「全部」；切到「图片反推」缩小到 image 源（seed 首条 rh-img-1 = succeeded）。
  await panel.getByRole("button", { name: "图片反推" }).click();
  const firstItem = panel.getByRole("listitem").first();
  await expect(firstItem).toBeVisible();

  // 打开该条详情 → 弹窗内是复用的 ReversePromptResultView（带入按钮在这里，列表卡上没有：BE 列表项无 result）。
  await firstItem.getByRole("button", { name: "查看详情" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("反推详情")).toBeVisible({ timeout: 15_000 });

  // 历史场景不给「重新反推」入口（二次扣 100 积分且无计费门）。
  await expect(dialog.getByRole("button", { name: "重新反推" })).toHaveCount(0);

  // 🔴 带入 —— 本条的核心：跨四层的那条链在此通电。
  await dialog.getByRole("button", { name: "带入 · AI 模特" }).click();
  // REVERSE-DEEP-UI-0001 · D3-④：确认弹窗在**另一个 portal**（不在历史 dialog 作用域内）→ 用 page 取。
  await page.getByRole("button", { name: "确认带入" }).click();

  // 两层弹窗都关闭（确认窗自身 + 历史详情窗；带入后让用户看见被预填的表单）。
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // 🔴 断言一：activate 首挂 —— 电商图面板此刻才被挂载**并激活**（首屏还 toHaveCount(0)）。
  await expect(page.getByTestId("panel-ecom_image")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "电商图", exact: true })).toHaveAttribute("aria-pressed", "true");

  // 🔴 断言二：落点 —— 子工具切到「AI 模特」且 custom 真的收到 BE 的 ecom_model.extra_prompt。
  //    （toHaveValue 不校验可见性 → 先断可见，否则「切没切到子工具」不被验证。）
  const custom = page.getByTestId("panel-ecom_image").locator("#ecom-model-custom");
  await expect(custom).toBeVisible();
  await expect(custom).toHaveValue("工作室柔光、简洁白底、突出质感");

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
