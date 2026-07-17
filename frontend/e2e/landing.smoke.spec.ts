import { expect, test, type Page } from "@playwright/test";

/**
 * LANDING-ENTRY-UI-0001 交互冒烟（生产构建 next start）：
 *  ① 未登录进站根 `/` → 落地页 /landing（Hero/七模块/样片/五步/CTA/页脚齐备；CTA 指 /register 与 /login）；
 *  ② 「登录控制台」→ /login 登录 → 回控制台 `/`（工作台可见=已登录进站零回归）；
 *  ③ 已登录显式访问 /landing → 顶栏头像态，下拉「进控制台」回 `/`；
 *  ④ 移动端 375 顶栏入口可见；⑤ prefers-reduced-motion → 流光/粒子动画降级 none。
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

test("未登录进站=落地页；登录→控制台；已登录访 /landing=头像态；移动端；reduced-motion 降级", async ({ page }) => {
  const g = watch(page);

  // ① 未登录进站根 `/` → 落地页。
  await page.goto("/");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
  await page.waitForURL(/\/landing$/, { timeout: 15_000 });
  await expect(page.getByRole("heading", { level: 1, name: "企业级 AI 短视频工厂" })).toBeVisible();
  // 结构齐备：七模块 + 样片 + 五步 + 页脚。
  await expect(page.getByRole("heading", { name: "七大生产模块" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "数字人口播", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "更多能力持续上线" })).toBeVisible();
  await expect(page.getByText("© 华鼎 · 企业级 AI 短视频引擎")).toBeVisible();
  // 数据条可信表述，无「500+」。
  await expect(page.getByText("7 大模块")).toBeVisible();
  await expect(page.getByText("500+")).toHaveCount(0);
  // ADMIN-VIP-GATE-UI-0001：0 余额文案——CTA 无「免费」、注册 CTA 区改「注册后联系我们开通额度」。
  await expect(page.getByText("免费")).toHaveCount(0);
  await expect(page.getByText("立即免费注册")).toHaveCount(0);
  await expect(page.getByText("注册后联系我们开通额度")).toBeVisible();
  // CTA 指向（现为「立即注册」）。
  const hero = page.locator("main");
  await expect(hero.getByRole("link", { name: "立即注册" }).first()).toHaveAttribute("href", "/register");
  await expect(hero.getByRole("link", { name: "登录控制台" })).toHaveAttribute("href", "/login");
  // 顶栏未登录态：登录 + 立即注册。
  const header = page.locator("header");
  await expect(header.getByRole("link", { name: "登录", exact: true })).toBeVisible();
  await expect(header.getByRole("link", { name: "立即注册" })).toBeVisible();

  // ⑤ reduced-motion：流光标题动画降级 none（CSS 守卫生效）。
  await page.emulateMedia({ reducedMotion: "reduce" });
  const animation = await page
    .locator("h1.landing-flow-text")
    .evaluate((el) => getComputedStyle(el).animationName);
  expect(animation).toBe("none");
  await page.emulateMedia({ reducedMotion: null });

  // ② 登录控制台 → /login → 登录 → 回控制台 `/`（工作台）。
  await hero.getByRole("link", { name: "登录控制台" }).click();
  await page.waitForURL(/\/login$/, { timeout: 15_000 });
  const inputs = page.locator("form input");
  await inputs.nth(0).fill("huading");
  await inputs.nth(1).fill("qa@huading.test");
  await inputs.nth(2).fill("pw123456");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
  await expect(page.getByRole("button", { name: "生成视频" })).toBeVisible({ timeout: 20_000 });

  // ③ 已登录显式访问 /landing → 头像态（首字圆标），下拉「进控制台」回 `/`。
  await page.goto("/landing");
  const avatar = page.getByRole("button", { name: "账户菜单" });
  await expect(avatar).toBeVisible({ timeout: 15_000 });
  await avatar.click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await page.getByRole("link", { name: "进控制台" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 15_000 });
  await expect(page.getByRole("button", { name: "生成视频" })).toBeVisible({ timeout: 20_000 });

  // ④ 移动端 375：退出后回落地页，顶栏入口两态可见（锚点隐藏、登录按钮在）。
  await page.setViewportSize({ width: 375, height: 812 });
  await page.evaluate(() => window.localStorage.removeItem("huading.session"));
  await page.goto("/landing");
  await expect(page.locator("header").getByRole("link", { name: "登录", exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { level: 1, name: "企业级 AI 短视频工厂" })).toBeVisible();

  // ⑥ LANDING-CONTACT-UI-0001 · 联系区双端形态（断点显隐 jsdom 钉不了，只有真浏览器能钉）。
  // 仍在 375 移动端：显示「保存图 → 微信扫一扫从相册选取」+ 保存按钮；PC 扫码引导隐藏。
  const qrImg = page.getByRole("img", { name: /客服微信二维码/ });
  await qrImg.scrollIntoViewIfNeeded();
  await expect(qrImg).toBeVisible();
  await expect
    .poll(async () => qrImg.evaluate((el) => (el as HTMLImageElement).naturalWidth), { timeout: 10_000 })
    .toBeGreaterThan(0); // 图真的部署且可解码（不是 404 裂图）
  await expect(page.getByText(/保存二维码图片，打开微信/)).toBeVisible();
  const saveLink = page.getByRole("link", { name: /保存二维码/ });
  await expect(saveLink).toBeVisible();
  await expect(saveLink).toHaveAttribute("download", /.+/);
  await expect(page.getByText(/打开手机微信「扫一扫」/)).toBeHidden();

  // 桌面 1280：PC 扫码引导显示；保存按钮隐藏（PC 主路径是直接扫屏）。页脚「联系」锚到 #contact。
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.getByText(/打开手机微信「扫一扫」/)).toBeVisible();
  await expect(saveLink).toBeHidden();
  await expect(page.getByRole("link", { name: "联系", exact: true })).toHaveAttribute("href", "#contact");

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
