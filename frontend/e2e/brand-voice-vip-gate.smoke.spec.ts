import { expect, test, type Page } from "@playwright/test";

/**
 * ADMIN-VIP-GATE-UI-0001 §二之二 · FIX1 交互冒烟（生产构建，走 MSW）：VIP entitlement 按「角色 + 套餐」派生
 * （localStorage hd_mock_role / hd_mock_plan → login/me 派生 permissions，镜像真实 BE）：
 *  ① creator + free（无 entitlement）：/brand-voices doubao「升级版 VIP」置灰 + 「开通 huading plan 后可创建」，
 *     缺省切 cosyvoice；工作台「选我的音色」doubao 品牌音色置灰 + 「开通 huading plan 后可用」（区别于「槽位空」）；移动端 375。
 *  ② creator + huading（真实付费用户）：doubao 通路**放行**——创建卡可选、无锁提示（不误伤）。
 * 全程无 #130 / 无 /api/api 双前缀。需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
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

// 以指定「角色 + 套餐」登录：登录前置双旋钮 → mock login/me 按套餐派生 permissions。
async function loginAs(page: Page, role: "admin" | "creator", plan: "huading" | "free") {
  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
  await page.evaluate(
    ([r, p]) => {
      // 普通租户（platform=0）→ voice_clone_vip 仅由 huading 套餐决定；否则默认平台方会一律授予、free 也不置灰。
      window.localStorage.setItem("hd_mock_platform", "0");
      window.localStorage.setItem("hd_mock_role", r);
      window.localStorage.setItem("hd_mock_plan", p);
    },
    [role, plan]
  );
  const inputs = page.locator("form input");
  await inputs.nth(0).fill("huading");
  await inputs.nth(1).fill("qa@huading.test");
  await inputs.nth(2).fill("pw123456");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
}

test("creator + free（无 entitlement）：doubao 通路创建/选择均置灰 + 提示；免费档可用；移动端", async ({ page }) => {
  const g = watch(page);
  await loginAs(page, "creator", "free");

  // ① /brand-voices 创建：doubao 卡置灰 + 提示；缺省切 cosyvoice（免费档可用）。
  await page.getByRole("link", { name: /我的品牌音色/ }).click();
  await page.waitForURL(/\/brand-voices$/, { timeout: 30_000 });
  const doubaoCard = page.locator('button:has-text("升级版 VIP 永久高端定制音色")');
  const cosyCard = page.locator('button:has-text("免费开通私人专属音色")');
  await expect(doubaoCard).toBeVisible({ timeout: 15_000 });
  await expect(doubaoCard).toBeDisabled(); // VIP 门禁置灰
  await expect(page.getByText("开通 huading plan 后可创建")).toBeVisible();
  await expect(cosyCard).toBeEnabled();
  await expect(cosyCard).toHaveAttribute("aria-pressed", "true"); // 缺省自动切 cosyvoice

  // ② 工作台口播「选我的音色」：doubao 品牌音色（我的主播音）置灰 + 「开通 huading plan 后可用」。
  await page.goto("/");
  await expect(page.getByRole("button", { name: "生成视频" })).toBeVisible({ timeout: 20_000 });
  const doubaoVoice = page.locator('button:has-text("我的主播音")');
  await expect(doubaoVoice).toBeVisible({ timeout: 15_000 });
  await expect(doubaoVoice).toBeDisabled();
  await expect(page.getByText("开通 huading plan 后可用")).toBeVisible();
  // 区别于「暂无可用音色槽位」——不应出现槽位空文案（这是"有权限池空"，非本场景）。
  await expect(page.getByText(/暂无可用音色槽位/)).toHaveCount(0);

  // ③ 移动端 375：创建页 doubao 仍置灰。
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/brand-voices");
  await expect(page.getByText("开通 huading plan 后可创建")).toBeVisible({ timeout: 15_000 });

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});

test("creator + huading（真实付费用户）：doubao 通路放行——创建卡可选、无锁提示（不误伤）", async ({ page }) => {
  const g = watch(page);
  await loginAs(page, "creator", "huading");

  await page.getByRole("link", { name: /我的品牌音色/ }).click();
  await page.waitForURL(/\/brand-voices$/, { timeout: 30_000 });
  const doubaoCard = page.locator('button:has-text("升级版 VIP 永久高端定制音色")');
  await expect(doubaoCard).toBeVisible({ timeout: 15_000 });
  // 付费用户：doubao 卡**可选**、无锁提示。
  await expect(doubaoCard).toBeEnabled();
  await expect(page.getByText("开通 huading plan 后可创建")).toHaveCount(0);

  // 工作台「选我的音色」：doubao 品牌音色可选、无锁提示。
  await page.goto("/");
  await expect(page.getByRole("button", { name: "生成视频" })).toBeVisible({ timeout: 20_000 });
  const doubaoVoice = page.locator('button:has-text("我的主播音")');
  await expect(doubaoVoice).toBeVisible({ timeout: 15_000 });
  await expect(doubaoVoice).toBeEnabled();
  await expect(page.getByText("开通 huading plan 后可用")).toHaveCount(0);

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
