import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const rows = [
  ["script_generate", "AI 口播文案", "1 积分/次", "价格完成，待上线", "前台可见"],
  ["scene_prompt", "画面提示词", "30 积分/次", "价格完成，待上线", "前台可见"],
  ["ecom_cutout", "电商抠图", "80 积分/张", "价格完成，待上线", "前台可见"],
  ["ecom_model", "AI 模特图", "80 积分/张", "价格完成，待上线", "前台可见"],
  ["doubao_brand_voice", "豆包品牌音色", "30000 积分/个/365 天", "价格完成，待上线", "前台可见"],
  ["cosyvoice", "CosyVoice", "创建免费；使用时 0.1 积分/字", "价格完成，待上线", "前台可见"],
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

test("pricing workbench computes six waiting, three deferred and one offline", async ({ page }) => {
  const errors = watchErrors(page);
  const html = await readFile(resolve(process.cwd(), "../docs/04-UIUX设计/定价工作台.html"), "utf8");
  await page.setContent(html);

  await expect(page.getByText("生产环境冻结；以下状态不代表已生效")).toBeVisible();
  await expect(page.getByTestId("count-priced-waiting-release")).toHaveText("6");
  await expect(page.getByTestId("count-deferred-unpriced")).toHaveText("3");
  await expect(page.getByTestId("count-offline")).toHaveText("1");
  await expect(page.getByText("先冻结，人工交付后结算")).toBeVisible();

  for (const [key, name, price, closure, visibility] of rows) {
    const row = page.getByTestId(`pricing-row-${key}`);
    await expect(row).toBeVisible();
    await expect(row).toContainText(name);
    await expect(row).toContainText(price);
    await expect(row).toContainText(closure);
    await expect(row).toContainText(visibility);
  }

  await expect(page.getByTestId("pricing-row-publish_draft")).toHaveAttribute("data-visibility", "hidden");
  await expect(page.getByTestId("pricing-row-marketing_poster")).toHaveAttribute("data-closure", "offline");
  await expect(page.getByText("已上线", { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  expect(errors).toEqual([]);
});
