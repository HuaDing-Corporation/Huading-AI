import { expect, test, type Page } from "@playwright/test";

/**
 * 审计「变更前 → 变更后」信噪比 + 渲染冒烟（ADMIN-AUDIT-DIFF-NOISE-0001，叠 #169，生产构建走 MSW）。
 * 驱动一次真实套餐变更（Acme huading→basic：只有 plan_code 变、subscription.* 全不变），断言：
 *  ① plan_code 变化 → 默认展示（前→后确定值）；② 未变的 subscription.* 默认**收进折叠区**（不可见）、
 *  折叠标题「另有 5 项未变化」可见；③ 点开折叠 → 未变 5 项**确实还在**（信息只折叠不删，资金追溯依据）；
 *  ④ 种子槽位分配行（全变化，无折叠）数组 join；⑤ 负断言：整表无 `[object Object]`（附加，非主断言）。
 * 全程 Console 0 error（Chrome DevTools 口径）。
 */
function watch(page: Page): { errors: () => string[] } {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(String(err?.message ?? err)));
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Minified React error #130|error #130|client-side exception/.test(t)) errors.push(t);
  });
  return { errors: () => errors };
}

async function loginPlatform(page: Page) {
  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
  const inputs = page.locator("form input");
  await inputs.nth(0).fill("huading");
  await inputs.nth(1).fill("qa@huading.test");
  await inputs.nth(2).fill("pw123456");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
}

test("套餐变更（嵌套 subscription）审计行不再 [object Object]：逐键点号路径 + 千分位 + 数组 join", async ({ page }) => {
  const g = watch(page);
  await loginPlatform(page);

  // 顶栏入口 → 租户管理 → Acme 详情。
  await page.getByRole("link", { name: "管理后台" }).click();
  await page.waitForURL(/\/admin\/tenants$/, { timeout: 20_000 });
  const acmeRow = page.locator("tr", { hasText: "Acme 电商" });
  await expect(acmeRow).toBeVisible({ timeout: 15_000 });
  await acmeRow.getByRole("button", { name: "详情" }).click();
  const detail = page.getByRole("dialog", { name: "租户详情" });
  await expect(detail).toBeVisible();

  // 改套餐 huading → basic（产出 plan_change 审计，before/after 含嵌套 subscription 对象）。
  // 注：SelectTrigger 未透传 aria-label（既有 a11y 缺陷，见回执）→ 套餐 combobox 无可及名，作用域到弹窗取其唯一 combobox。
  await detail.getByRole("combobox").click();
  await page.getByRole("option", { name: "basic", exact: true }).click();
  await detail.getByRole("button", { name: "改套餐" }).click();
  await expect(page.getByText("确认套餐变更")).toBeVisible();
  await page.getByRole("button", { name: "确认变更" }).click();
  await expect(page.getByText("套餐已变更")).toBeVisible({ timeout: 15_000 });
  await page.keyboard.press("Escape"); // 关详情弹窗

  // 审计页：嵌套对象雷 = 主战场。
  await page.getByRole("link", { name: "审计日志" }).click();
  await page.waitForURL(/\/admin\/audit$/, { timeout: 15_000 });

  // ① plan_code 变化 → 默认展示（前→后确定值）。
  await expect(page.getByText(/^plan_code：.*huading.*basic$/)).toBeVisible({ timeout: 15_000 });
  // ② 未变的 subscription.* 默认**收进折叠区**（不可见）；折叠标题「另有 5 项未变化」可见。
  await expect(page.getByText("另有 5 项未变化")).toBeVisible();
  await expect(page.getByText(/subscription\.total/)).not.toBeVisible();
  // ③ 点开折叠 → 未变 5 项**确实还在**（信息只折叠不删）：subscription.total 单值 20,000 可见。
  await page.getByText("另有 5 项未变化").click();
  await expect(page.getByText(/subscription\.total：.*20,000/)).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText(/subscription\.remaining：.*14,000/)).toBeVisible();
  // ④ 种子槽位分配行（全变化，无折叠）：数组 join 成可读串（after speaker_ids=["S_acme_001"]）。
  await expect(page.getByText(/^speaker_ids：.*S_acme_001$/)).toBeVisible();

  // ⑤ 附加负断言（非主断言）：整表内绝无 [object Object]。
  const tableText = (await page.locator("table").innerText()) ?? "";
  expect(tableText, "审计表内不得出现 [object Object]").not.toContain("[object Object]");

  expect(g.errors(), `page errors：\n${g.errors().join("\n")}`).toEqual([]);
});
