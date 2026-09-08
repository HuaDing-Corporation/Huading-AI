import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const rows = [
  ["script_generate", "AI 口播文案", "1 积分/次", "价格已部署，验收待闭环", "前台可见"],
  ["scene_prompt", "画面提示词", "30 积分/次", "价格已部署，验收待闭环", "前台可见"],
  ["ecom_cutout", "电商抠图", "80 积分/张", "价格已部署，验收待闭环", "前台可见"],
  ["ecom_model", "AI 模特图", "80 积分/张", "价格已部署，验收待闭环", "前台可见"],
  ["doubao_brand_voice", "豆包品牌音色", "30000 积分/个/365 天", "价格已部署，验收待闭环", "前台可见"],
  ["cosyvoice", "CosyVoice", "创建免费；使用时 0.1 积分/字", "价格已部署，验收待闭环", "前台可见"],
  ["seedance_t2v", "seedance_t2v", "未定价", "延期处理／尚未闭环", "前台可见"],
  ["static_template", "static_template", "未定价", "延期处理／尚未闭环", "前台可见"],
  ["publish_draft", "发布草稿", "未定价", "延期处理／尚未闭环", "保持隐藏"],
  ["marketing_poster", "电商营销海报", "未定价", "已下线", "保持隐藏"]
] as const;

function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error.message ?? error)));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  return errors;
}

// setContent only: no app server, account, paid action or external API is needed.
test.beforeEach(async ({ page }) => {
  await page.route("**/*", (route) => route.abort());
});

test("pricing workbench separates deployed prices, verified payments and unpublished repairs", async ({ page }) => {
  const errors = watchErrors(page);
  const html = await readFile(resolve(process.cwd(), "../docs/04-UIUX设计/定价工作台.html"), "utf8");
  await page.setContent(html);

  await expect(page.getByRole("status")).toContainText("价格已部署；验收待闭环，修复补丁尚未发布");
  await expect(page.getByTestId("production-version")).toHaveText("ad54dc80");
  await expect(page.locator("time")).toHaveAttribute("datetime", "2026-09-08");
  await expect(page.getByTestId("count-deployed-pending-acceptance")).toHaveText("6");
  await expect(page.getByTestId("count-deferred-unpriced")).toHaveText("3");
  await expect(page.getByTestId("count-offline")).toHaveText("1");
  await expect(page.getByTestId("count-real-settled")).toHaveText("4");
  await expect(page.getByTestId("real-settled-credits")).toHaveText("191");
  await expect(page.getByTestId("current-held-credits")).toHaveText("0");
  await expect(page.getByTestId("acceptance-scope")).toContainText("不代表全平台 E2E 完成");
  await expect(page.getByTestId("repair-status")).toContainText("本地待发布：后端已审过，前端仍审中");

  for (const [key, name, price, closure, visibility] of rows) {
    const row = page.getByTestId(`pricing-row-${key}`);
    await expect(row).toBeVisible();
    await expect(row).toContainText(name);
    await expect(row).toContainText(price);
    await expect(row).toContainText(closure);
    await expect(row).toContainText(visibility);
    await expect(row.locator(".acceptance")).not.toBeEmpty();
    await expect(row.locator(".next-step")).not.toBeEmpty();
  }

  for (const [key, amount] of [["script_generate", "1"], ["scene_prompt", "30"], ["ecom_cutout", "80"], ["ecom_model", "80"]]) {
    await expect(page.getByTestId(`pricing-row-${key}`)).toContainText(`真实请求 1 次，已结清 ${amount} 积分`);
  }
  for (const key of ["ecom_cutout", "ecom_model"]) {
    await expect(page.getByTestId(`pricing-row-${key}`)).toContainText("处理中查询 500、确认框与余额修复尚未发布");
  }
  const doubao = page.getByTestId("pricing-row-doubao_brand_voice");
  await expect(doubao).toContainText("先冻结，人工官方注册交付后结算；拒绝则释放");
  await expect(doubao).toContainText("隔离验收待结果；真实人工注册未做");
  const cosy = page.getByTestId("pricing-row-cosyvoice");
  await expect(cosy).toContainText("字符费随父视频成功结算");
  await expect(cosy).toContainText("隔离验收待结果；真实父视频未授权");
  for (const key of ["seedance_t2v", "static_template"]) {
    await expect(page.getByTestId(`pricing-row-${key}`)).toContainText("暂缓定价，不下线");
  }
  await expect(page.getByTestId("pricing-row-publish_draft")).toHaveAttribute("data-visibility", "hidden");
  await expect(page.getByTestId("pricing-row-marketing_poster")).toHaveAttribute("data-closure", "offline");
  await expect(page.locator("body")).not.toContainText("生产环境冻结");
  await expect(page.locator("body")).not.toContainText("价格完成，待上线");
  await expect(page.locator("body")).not.toContainText("已修复上线");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test("summary follows row status instead of keeping a hardcoded deployed count", async ({ page }) => {
  const html = await readFile(resolve(process.cwd(), "../docs/04-UIUX设计/定价工作台.html"), "utf8");
  // Controlled fixture: one capability moves back to deferred. The rendered row
  // and aggregate must both change; this catches a separately hardcoded summary.
  await page.setContent(html.replace('closure: "deployed_pending_acceptance"', 'closure: "deferred_unpriced"'));
  await expect(page.getByTestId("pricing-row-script_generate")).toHaveAttribute("data-closure", "deferred_unpriced");
  await expect(page.getByTestId("count-deployed-pending-acceptance")).toHaveText("5");
  await expect(page.getByTestId("count-deferred-unpriced")).toHaveText("4");
  await expect(page.getByTestId("count-offline")).toHaveText("1");
});
