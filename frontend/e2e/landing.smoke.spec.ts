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

  // 🔴 LANDING-CONTACT-UI-0001 · FIX1 · P1-2：已登录 TopBar 在窄屏及响应式恢复边界无横向溢出。
  // 这条路以前从没走过——现有 e2e 只在**退出后**切 375px（下面 ④），已登录顶栏永远没被移动端测过，
  // 于是「加了开通额度入口把顶栏撑爆、退出按钮被挤出首屏」逃过了 CI（Codex B 实测 scroll 457px）。
  // 变异：把完整品牌/搜索/动作文字/单行布局提前恢复到 sm(640px) → 640/641/768 的 scrollWidth
  // 立即大于视口；只测 320/375 与 1280 会漏掉这个断点跳变。
  for (const width of [320, 360, 375, 639, 640, 641, 768, 1023]) {
    await page.setViewportSize({ width, height: 812 });
    await page.goto("/"); // 确保在工作台（TopBar 所在）
    await expect(page.getByRole("button", { name: "生成视频" })).toBeVisible({ timeout: 20_000 });
    // QuotaBadge 异步请求：必须等 mock 的真实长数字余额落屏后再量宽。少这一步会在余额尚未渲染时
    // 假绿，只有并行 E2E 稍慢时才偶发抓到 844/1000 把头像推到视口之外。
    await expect(page.getByText("当前余额 844/1000", { exact: true })).toBeVisible();
    await expect(page.getByText("运行任务冻结 36", { exact: true })).toBeVisible();
    await expect(page.getByText("人工交付冻结 30000", { exact: true })).toBeVisible();
    await expect(page.getByText("待下期到账 0", { exact: true })).toBeVisible();
    // 无横向溢出：文档滚动宽度 = 视口宽度（多 1px 都算溢出）。
    const scrollW = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(scrollW, `已登录 ${width}px 顶栏横向溢出：scrollWidth=${scrollW} > ${width}`).toBeLessThanOrEqual(width);
    // 管理后台 / 开通额度 / 退出均是关键动作：允许紧凑成图标或换行，但必须完整留在视口内。
    for (const name of ["管理后台", "开通额度", "退出登录"]) {
      const action = page.getByRole(name === "管理后台" ? "link" : "button", { name }).first();
      await expect(action).toBeVisible();
      const box = await action.boundingBox();
      expect(
        box && box.x >= 0 && box.x + box.width <= width,
        `「${name}」不在 ${width}px 首屏内：${JSON.stringify(box)}`
      ).toBe(true);
    }
    // 头像是装饰性 div（金圆标，非按钮），同样必须完整留在当前视口。
    const avatarBox = await page
      .locator(".rounded-full.bg-grad-gold")
      .first()
      .evaluate((el) => {
        const r = el.getBoundingClientRect();
        return { x: r.x, right: r.right };
      });
    expect(
      avatarBox.x >= 0 && avatarBox.right <= width,
      `头像不在 ${width}px 首屏内：${JSON.stringify(avatarBox)}`
    ).toBe(true);

    // 紧凑套件持续覆盖到 1023px；桌面套件在下面的 1280px 真实桌面视口单独验收。
    await expect(page.getByText("华鼎 AI", { exact: true })).toBeHidden();
    await expect(page.getByRole("textbox", { name: "搜索" })).toBeHidden();
    await expect(page.getByRole("button", { name: "通知" })).toBeHidden();
    await expect(page.getByRole("button", { name: "设置" })).toBeHidden();
  }
  await page.setViewportSize({ width: 1280, height: 800 }); // 切回桌面继续 ③
  await page.goto("/");
  await expect(page.getByText("当前余额 844/1000", { exact: true })).toBeVisible();
  await expect(page.getByText("运行任务冻结 36", { exact: true })).toBeVisible();
  await expect(page.getByText("人工交付冻结 30000", { exact: true })).toBeVisible();
  await expect(page.getByText("待下期到账 0", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await expect(page.getByText("华鼎 AI", { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "搜索" })).toBeVisible();
  await expect(page.getByRole("button", { name: "通知" })).toBeVisible();
  await expect(page.getByRole("button", { name: "设置" })).toBeVisible();

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
