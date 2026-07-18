import { expect, test, type Page } from "@playwright/test";

/**
 * AUTH-UI-0001 交互冒烟（生产构建 next start，走 MSW）：
 *  ① 登录页新文案（「用户名」label、无副标题「输入用户与账号以继续」）+ 「去注册」互链；
 *  ② 注册页各客户端校验（非法用户名 / 弱密码 拦截）；③ slug="taken" → 409 friendly「已被占用」不泄裸串；
 *  ④ 合法注册 → 落 token 进控制台（工作台可见）；⑤ 注册↔登录互链；⑥ 移动端 375 注册表单可用。
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

async function ready(page: Page) {
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
}

test("登录新文案 + 互链；注册校验 / slug 占用 friendly / 成功进控制台；移动端", async ({ page }) => {
  const g = watch(page);

  // ① 登录页文案：「用户名」label、无旧副标题、有「去注册」。
  await page.goto("/login");
  await ready(page);
  await expect(page.getByText("用户名")).toBeVisible();
  await expect(page.getByText("用户标识 (tenant slug)")).toHaveCount(0);
  await expect(page.getByText("输入用户与账号以继续")).toHaveCount(0);
  await page.getByRole("link", { name: "去注册" }).click();
  await page.waitForURL(/\/register$/, { timeout: 15_000 });
  await expect(page.getByRole("button", { name: "注册" })).toBeVisible();

  // ② 客户端校验：非法用户名（大写）→ 拦截。
  await page.getByLabel("用户名").fill("BadSlug");
  await page.getByLabel("团队 / 公司名称").fill("华鼎");
  await page.getByLabel("邮箱").fill("a@b.com");
  await page.getByLabel("密码").fill("pw123456");
  await page.getByRole("button", { name: "注册" }).click();
  await expect(page.getByText(/用户名只能用小写字母/)).toBeVisible();

  // ③ slug="taken" → 409 friendly「已被占用」，不泄英文原串。
  await page.getByLabel("用户名").fill("taken");
  await page.getByRole("button", { name: "注册" }).click();
  await expect(page.getByText("该用户名已被占用，请换一个")).toBeVisible();
  await expect(page.getByText(/already taken/i)).toHaveCount(0);

  // ④ 合法注册 → 落 token 进控制台（工作台生成按钮可见）。
  await page.getByLabel("用户名").fill("huading-new");
  await page.getByRole("button", { name: "注册" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
  await expect(page.getByRole("button", { name: "生成视频" })).toBeVisible({ timeout: 20_000 });

  // ④b LANDING-CONTACT-UI-0001 硬门：**走完注册流程后**欢迎横幅真的出现（不是"组件存在"）。
  // 真实链路：注册页落 localStorage 标记 → replace("/") → 工作台读标记渲染横幅。
  await expect(page.getByText("注册成功，欢迎加入华鼎！")).toBeVisible();
  // 点「查看微信二维码」→ 弹窗含真实二维码图（静态资源真的部署在 /wechat-qr.png）
  await page.getByRole("button", { name: "查看微信二维码" }).click();
  const qr = page.getByRole("dialog").getByRole("img", { name: /客服微信二维码/ });
  await expect(qr).toBeVisible();
  // 图真的加载成功（naturalWidth>0 = 静态资源存在且可解码，不是 404 裂图）
  await expect
    .poll(async () => qr.evaluate((el) => (el as HTMLImageElement).naturalWidth), { timeout: 10_000 })
    .toBeGreaterThan(0);
  await page.getByRole("dialog").getByRole("button", { name: "关闭" }).click();
  // 关闭横幅 → 消失；「能再次找到」：顶栏常驻「开通额度」仍能打开二维码弹窗
  await page.getByRole("button", { name: "我知道了" }).click();
  await expect(page.getByText("注册成功，欢迎加入华鼎！")).toHaveCount(0);
  await page.getByRole("button", { name: "开通额度" }).click();
  await expect(page.getByRole("dialog").getByRole("img", { name: /客服微信二维码/ })).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "关闭" }).click();
  // 刷新 → 横幅不再出现（主动关闭是持久的，不纠缠用户）
  await page.reload();
  await expect(page.getByRole("button", { name: "生成视频" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("注册成功，欢迎加入华鼎！")).toHaveCount(0);

  // ⑤ 退出后：注册页「去登录」互链回登录页。
  await page.evaluate(() => window.localStorage.removeItem("huading.session"));
  await page.goto("/register");
  await page.getByRole("link", { name: "去登录" }).click();
  await page.waitForURL(/\/login$/, { timeout: 15_000 });

  // ⑥ 移动端 375：注册表单字段可用。
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/register");
  await expect(page.getByLabel("用户名")).toBeVisible();
  await expect(page.getByLabel("姓名（选填）")).toBeVisible();

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
