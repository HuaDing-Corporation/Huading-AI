import { expect, test, type Page } from "@playwright/test";

/**
 * HISTORY-VIDEO-DIALOG-UI-0001 交互冒烟（生产构建 next start，真走 MSW）：三视频 tab 的大屏播放 + 详情弹窗。
 * jsdom 测不了的三件事在这里验：真实 Radix Portal 的挂载/卸载、真 <video> 元素、真 AX 树。
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
  // ⚠️ /login 而非 "/"：未登录进站根已分流 /landing（#151/#153 栽过两次）。
  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
  const inputs = page.locator("form input");
  await inputs.nth(0).fill("huading");
  await inputs.nth(1).fill("qa@huading.test");
  await inputs.nth(2).fill("pw123456");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
}

const ecomPanel = (page: Page) => page.getByRole("tabpanel", { name: "电商视频历史" });

test("视频大屏 overlay：内联播放 / 不 autoplay / 关闭即卸载 / AX 树可及", async ({ page }) => {
  const g = watch(page);
  await login(page);
  await page.getByRole("tab", { name: "电商视频历史" }).click();
  await expect(ecomPanel(page)).toBeVisible({ timeout: 15_000 });

  // 关闭态零残留：没点之前不该有任何 dialog。
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await ecomPanel(page).getByRole("button", { name: "播放视频" }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  // 「大图」对视频 = overlay 内内联播放（不是放大静止封面）。
  const video = dialog.locator("video");
  await expect(video).toBeVisible();
  await expect(video).toHaveAttribute("controls", "");
  // 有声内容由用户发起 → 不 autoplay。
  await expect(video).not.toHaveAttribute("autoplay", /.*/);

  // AX 树（CDP）：overlay 是 modal dialog，且 <video> 有可及名（否则读屏只报「video」）。
  const client = await page.context().newCDPSession(page);
  await client.send("Accessibility.enable");
  const { nodes } = (await client.send("Accessibility.getFullAXTree")) as unknown as {
    nodes: Array<{ role?: { value?: string }; name?: { value?: string } }>;
  };
  const roles = nodes.map((n) => n.role?.value ?? "");
  expect(roles).toContain("dialog");
  const videoNode = nodes.find((n) => n.role?.value === "video" || n.role?.value === "Video");
  expect(videoNode?.name?.value ?? "", "「<video> 必须有可及名，否则读屏只报 video」").not.toBe("");

  // 🔴 关闭即卸载 —— 留着播 = 用户切走后还在响。
  await dialog.getByRole("button", { name: "关闭" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator("video[aria-label]")).toHaveCount(0); // overlay 里那个（带可及名）已从 DOM 消失

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});

test("视频详情弹窗：信息并集 + 「打开详情页」把跳转能力接回来", async ({ page }) => {
  const g = watch(page);
  await login(page);
  await page.getByRole("tab", { name: "电商视频历史" }).click();
  await expect(ecomPanel(page)).toBeVisible({ timeout: 15_000 });

  await ecomPanel(page).getByRole("button", { name: "查看详情" }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  // 信息并集：生成时间（卡片没显、图片详情弹窗有）+「分类」对应项 = 模式中文。
  await expect(dialog.getByText(/生成时间/)).toBeVisible();
  await expect(dialog.getByText(/电商视频历史/)).toBeVisible();

  // 🔴 跳转零回归：升级前卡片「查看详情」直接跳详情页；现在入口在弹窗内，能力一个不丢。
  await dialog.getByRole("button", { name: "打开详情页" }).click();
  await page.waitForURL(/\/videos\/.+/, { timeout: 15_000 });

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
