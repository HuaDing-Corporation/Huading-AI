import { expect, test, type Page } from "@playwright/test";

/**
 * ECOM-VIDEO-OPTIMIZE-UI-0001 电商带货视频优化 交互冒烟（生产构建 next start，真走 MSW 新契约）：
 *  ① 三个新控件渲染：产品图「张数选择器」、文案「字数档位」、「负面提示词」框（多图 picker 替代单图）。
 *  ② req1/决策2：主题去必填 —— 主题留空、仅上传 1 张产品图即可「生成视频」（新下限=产品图≥1 + 音色）。
 *  ③ req7/§4.2：无产品图时「AI 生成画面」禁用 + 提示；上传后可点 → scene_prompt + negative_prompt 各自自动填入。
 *  ④ 端到端提交：主题空 + 产品图 → 确认扣费 → 202（防假绿：mock 校验 product_image_keys≥1，接线断即报错）。
 * 主门禁：全程 Console 0（任何 error 级）/ 无 pageerror / 无 #130 白屏 / 无 /api/api 双前缀。
 * 需以 NEXT_PUBLIC_USE_MOCK=1 构建后 next start 运行（webServer 已配）。
 */
const PNG = { name: "p.png", mimeType: "image/png", buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]) };

async function login(page: Page): Promise<{ consoleErrors: () => string[]; pageErrors: () => string[]; doublePrefix: () => string[] }> {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const doublePrefix: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err?.message ?? err)));
  page.on("console", (msg) => {
    const t = msg.text();
    if (msg.type() === "error") consoleErrors.push(t); // 生产构建无 React dev 警告 → error 级即真问题（Console 0 门）
    if (/Minified React error #130|error #130|client-side exception/.test(t)) pageErrors.push(t);
  });
  page.on("request", (req) => {
    if (req.url().includes("/api/api")) doublePrefix.push(`${req.method()} ${req.url()}`);
  });

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
  return { consoleErrors: () => consoleErrors, pageErrors: () => pageErrors, doublePrefix: () => doublePrefix };
}

test("电商带货视频优化：新控件 + 主题可空提交 + AI生成画面门禁与自动填负面（Console 0）", async ({ page }) => {
  const g = await login(page);

  await page.getByRole("button", { name: "电商带货" }).click();
  const ecom = page.getByTestId("panel-seedance_i2v");

  // ① 三个新控件渲染（KEEPALIVE：限定到电商面板 scope）。
  await expect(ecom.getByText("产品图张数")).toBeVisible();
  await expect(ecom.getByText("文案长度")).toBeVisible();
  await expect(ecom.getByText("负面提示词（可选）")).toBeVisible();
  await expect(ecom.getByRole("button", { name: /上传产品图/ })).toBeVisible();

  // ③ 无产品图 → 「AI 生成画面」禁用 + 提示。
  const sceneBtn = ecom.getByRole("button", { name: "AI 生成画面" });
  await expect(sceneBtn).toBeDisabled();
  await expect(ecom.getByText("请先上传产品图，再生成画面")).toBeVisible();

  // 生成视频：无产品图时禁用 + 「请上传产品图」提示（主题留空亦不再拦）。
  const generate = ecom.getByRole("button", { name: /生成视频/ });
  await expect(generate).toBeDisabled();
  await expect(ecom.getByText("请上传产品图后再生成")).toBeVisible();

  // 上传 1 张产品图（主题留空）。
  await ecom.locator('input[type="file"]#product-image').setInputFiles(PNG);
  await expect(ecom.getByRole("button", { name: /上传产品图（1\/1）/ })).toBeVisible({ timeout: 15_000 });

  // ② 主题空 + 仅产品图 → 可生成。
  await expect(generate).toBeEnabled();

  // ③ 上传后「AI 生成画面」可点 → scene_prompt + negative_prompt 各自自动填入（MSW 新契约）。
  await expect(sceneBtn).toBeEnabled();
  await sceneBtn.click();
  await expect(ecom.locator("#scene-prompt")).toHaveValue(/白色大理石台面/, { timeout: 15_000 });
  await expect(ecom.locator("#ecom-negative-prompt")).toHaveValue(/水印/);

  // 字数档位可切（长）。
  await ecom.getByRole("button", { name: "长", exact: true }).click();
  await expect(ecom.getByRole("button", { name: "长", exact: true })).toHaveAttribute("aria-pressed", "true");

  // ④ 端到端提交：确认扣费 → 无错（mock 校验 product_image_keys≥1，接线断即 422 报错）。
  await generate.click();
  await expect(page.getByRole("heading", { name: "确定生成" })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "确定", exact: true }).click();
  // 提交成功后确认窗关闭、无错误提示（seedance_i2v + product_image_keys 被 mock 接受）。
  await expect(page.getByRole("heading", { name: "确定生成" })).toHaveCount(0, { timeout: 15_000 });

  expect(g.pageErrors(), `page errors（含 #130）：\n${g.pageErrors().join("\n")}`).toEqual([]);
  expect(g.consoleErrors(), `console errors：\n${g.consoleErrors().join("\n")}`).toEqual([]);
  expect(g.doublePrefix(), `/api/api 双前缀：\n${g.doublePrefix().join("\n")}`).toEqual([]);
});
