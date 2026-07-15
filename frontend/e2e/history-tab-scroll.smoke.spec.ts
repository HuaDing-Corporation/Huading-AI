import { expect, test, type Page } from "@playwright/test";

/**
 * HISTORY-IMAGE-TAB-UI-0001 承重：「切历史 tab 不跳到模块底部」（Chrome DevTools 根因坐实 = scroll anchoring
 * 随更高面板加载把 scrollY 推到页底；修法 tabs.tsx TabsContent `overflow-anchor:none`）。
 * 复现口径：矮视口 + 停在「数字人视频历史」（较矮面板、近页底）→ 真实 mouse.click 切到「电商视频历史」
 * （更高面板）。修好后 scrollY 不应大幅下跳、且「历史生成」模块仍在视口内。
 * 🔴 变异哨兵：删掉 tabs.tsx 的 overflow-anchor 修复 → 本用例必红（实测 baseline 跳 ~972px）。
 */
async function loginToWorkbench(page: Page) {
  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
  const inputs = page.locator("form input");
  await inputs.nth(0).fill("huading");
  await inputs.nth(1).fill("qa@huading.test");
  await inputs.nth(2).fill("pw123456");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
}

test("切历史 tab 不跳到模块底部（scroll anchoring 根因修复）", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 320 });
  await loginToWorkbench(page);
  await page.waitForTimeout(300);

  // 停在「数字人视频历史」（默认，较矮），把 tablist 顶到视口上缘附近
  const pos = await page.evaluate(() => {
    const tl = [...document.querySelectorAll('[role="tablist"]')].find((t) => t.getAttribute("aria-label") === "历史生成")!;
    const abs = window.scrollY + tl.getBoundingClientRect().top;
    window.scrollTo(0, Math.max(0, abs - 30));
    const t = [...tl.querySelectorAll('[role="tab"]')].find((x) => /电商视频历史/.test(x.textContent || ""))!;
    const r = t.getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), scrollY: Math.round(window.scrollY) };
  });

  // 真实鼠标点击（不经 Playwright auto-scroll）切到「电商视频历史」（更高面板）
  await page.mouse.click(pos.x, pos.y);
  await page.waitForTimeout(450);

  const after = await page.evaluate(() => {
    const tl = [...document.querySelectorAll('[role="tablist"]')].find((t) => t.getAttribute("aria-label") === "历史生成")!;
    const r = tl.getBoundingClientRect();
    return { scrollY: Math.round(window.scrollY), tablistTop: Math.round(r.top), viewportH: window.innerHeight };
  });

  const jumped = after.scrollY - pos.scrollY;
  // 修好后不应大幅下跳（baseline ~972px）；给足容差 150px 以吸收面板高度收敛等正常微调。
  expect(jumped, `切 tab 后 scrollY 下跳 ${jumped}px（应≈0，baseline ~972）`).toBeLessThan(150);
  // 且「历史生成」tablist 仍在视口内（没被推出屏幕）。
  expect(after.tablistTop, "切 tab 后历史模块 tablist 仍应在视口内").toBeLessThan(after.viewportH);
  expect(after.tablistTop).toBeGreaterThan(-80);
});
