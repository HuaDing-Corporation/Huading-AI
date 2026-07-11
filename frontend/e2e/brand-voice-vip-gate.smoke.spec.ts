import { expect, test, type Page } from "@playwright/test";

/**
 * ADMIN-VIP-GATE-UI-0001 §二之二 交互冒烟（生产构建，走 MSW）：非 huading（且非 admin）用户
 * （localStorage hd_mock_non_vip=1 → login/me 回 creator + 无 voice_clone_vip）：
 *  ① /brand-voices 创建：doubao「升级版 VIP」卡置灰 + 「开通 huading plan 后可创建」，缺省切 cosyvoice；免费档可用；
 *  ② 工作台口播「选我的音色」：doubao 品牌音色置灰 + 「开通 huading plan 后可用」（区别于「暂无可用音色槽位」）；
 *  ③ 移动端 375。全程无 #130 / 无 /api/api 双前缀。需 NEXT_PUBLIC_USE_MOCK=1 构建后 next start。
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

test("非 huading 用户：doubao 通路创建/选择均置灰 + 提示；免费档可用；移动端", async ({ page }) => {
  const g = watch(page);

  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
  // 关键：登录前置非授权 flag → mock login/me 回 creator + 无 voice_clone_vip。
  await page.evaluate(() => window.localStorage.setItem("hd_mock_non_vip", "1"));
  const inputs = page.locator("form input");
  await inputs.nth(0).fill("huading");
  await inputs.nth(1).fill("qa@huading.test");
  await inputs.nth(2).fill("pw123456");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });

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
