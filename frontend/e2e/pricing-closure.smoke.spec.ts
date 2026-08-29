import { expect, test, type Page } from "@playwright/test";

type Probe = {
  scriptSubmits: number;
  lookups: number;
  videoSubmits: number;
  estimateQuotes: string[];
  submitHeaders: Array<{ quote: string | null; key: string | null }>;
};

async function installBillingProbe(page: Page) {
  await page.addInitScript(() => {
    const target = window as typeof window & {
      __pricingProbe?: Probe;
      __dropNextScript?: boolean;
      __failNextVideoEstimate?: boolean;
    };
    target.__pricingProbe = { scriptSubmits: 0, lookups: 0, videoSubmits: 0, estimateQuotes: [], submitHeaders: [] };
    const original = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init);
      const url = new URL(request.url, location.href);
      const method = request.method.toUpperCase();
      const isScriptSubmit = method === "POST" && url.pathname.endsWith("/api/v1/scripts/generate");
      const isLookup = method === "GET" && url.pathname.includes("/api/v1/billing/operations/by-idempotency/");
      const isVideoSubmit = method === "POST" && url.pathname.endsWith("/api/v1/videos");
      let failedUpload = false;
      if (method === "POST" && url.pathname.endsWith("/api/v1/uploads/images")) {
        const form = await request.clone().formData().catch(() => null);
        const file = form?.get("file");
        failedUpload = file instanceof File && file.name.includes("__FAIL__");
      }
      if (isScriptSubmit) target.__pricingProbe!.scriptSubmits += 1;
      if (isVideoSubmit) target.__pricingProbe!.videoSubmits += 1;
      if (isScriptSubmit || isVideoSubmit) {
        target.__pricingProbe!.submitHeaders.push({
          quote: request.headers.get("X-Huading-Quote"),
          key: request.headers.get("Idempotency-Key")
        });
      }
      if (isLookup) {
        target.__pricingProbe!.lookups += 1;
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      if (target.__failNextVideoEstimate && method === "POST" && url.pathname.endsWith("/api/v1/videos/estimate")) {
        target.__failNextVideoEstimate = false;
        return new Response(JSON.stringify({ data: null, error: { code: "ESTIMATE_UNAVAILABLE", message: "报价暂不可用" }, request_id: "e2e" }), {
          status: 503,
          headers: { "Content-Type": "application/json" }
        });
      }
      const response = await original(request);
      if (failedUpload) {
        const payload = await response.clone().json() as { data: { asset_id: string } };
        payload.data.asset_id = `${payload.data.asset_id}__FAIL__`;
        return new Response(JSON.stringify(payload), {
          status: response.status,
          headers: response.headers
        });
      }
      if (method === "POST" && url.pathname.endsWith("/estimate")) {
        const payload = await response.clone().json().catch(() => null) as { data?: { quote_token?: string } } | null;
        if (payload?.data?.quote_token) target.__pricingProbe!.estimateQuotes.push(payload.data.quote_token);
      }
      if (isScriptSubmit && target.__dropNextScript) {
        target.__dropNextScript = false;
        throw new TypeError("Failed to fetch after supplier completion");
      }
      return response;
    };
  });
}

async function login(page: Page) {
  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, { timeout: 30_000 });
  const inputs = page.locator("form input");
  await inputs.nth(0).fill("huading");
  await inputs.nth(1).fill("qa@huading.test");
  await inputs.nth(2).fill("pw123456");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("http://localhost:3100/", { timeout: 30_000 });
  await expect(page.getByRole("button", { name: "生成视频" })).toBeVisible({ timeout: 20_000 });
}

function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (
      message.type() === "error" &&
      !message.text().includes("Failed to fetch after supplier completion") &&
      !message.text().includes("Failed to load resource: net::ERR_FAILED") &&
      !message.text().includes("status of 422") &&
      !message.text().includes("status of 503")
    ) errors.push(message.text());
  });
  return errors;
}

test("unknown script result recovers once, preserves billing headers and invalidates edited quotes", async ({ page }) => {
  const errors = watchErrors(page);
  await installBillingProbe(page);
  await login(page);

  const topic = page.locator("#video-topic");
  await topic.fill("首版定价主题");
  await page.getByRole("button", { name: "重写文案" }).click();
  await expect(page.getByText("服务端应付积分")).toBeVisible();
  const firstQuote = await page.evaluate(() => (window as typeof window & { __pricingProbe: Probe }).__pricingProbe.estimateQuotes.at(-1));
  await page.getByRole("button", { name: "取消" }).click();

  await topic.fill("编辑后的定价主题");
  await page.getByRole("button", { name: "重写文案" }).click();
  await expect(page.getByText("服务端应付积分")).toBeVisible();
  const secondQuote = await page.evaluate(() => (window as typeof window & { __pricingProbe: Probe }).__pricingProbe.estimateQuotes.at(-1));
  expect(secondQuote).toBeTruthy();
  expect(secondQuote).not.toBe(firstQuote);

  await page.evaluate(() => { (window as typeof window & { __dropNextScript: boolean }).__dropNextScript = true; });
  await page.getByRole("button", { name: "确认并继续" }).click();
  await expect(page.getByText("计费结果确认中")).toBeVisible();
  await expect(page.getByText("未扣款", { exact: false })).toHaveCount(0);
  await expect(page.getByText("已结算 1 积分")).toBeVisible();
  await expect(page.locator("#video-script")).toContainText("编辑后的定价主题");

  const probe = await page.evaluate(() => (window as typeof window & { __pricingProbe: Probe }).__pricingProbe);
  expect(probe.scriptSubmits).toBe(1);
  expect(probe.lookups).toBeGreaterThanOrEqual(1);
  expect(probe.submitHeaders).toHaveLength(1);
  expect(probe.submitHeaders[0].quote).toBe(secondQuote);
  expect(probe.submitHeaders[0].key).toMatch(/^[0-9a-f-]{36}$/i);
  expect(errors).toEqual([]);
});

test("video contracts expose legacy, deferred and CosyVoice billing while estimate failure stays closed", async ({ page }) => {
  const errors = watchErrors(page);
  await installBillingProbe(page);
  await login(page);

  await page.getByRole("button", { name: "视频生成", exact: true }).click();
  await page.locator("#vg-prompt").fill("普通视频估价");
  await page.getByRole("button", { name: "生成视频" }).click();
  await expect(page.getByText(/预计消耗\s*2\s*积分/)).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();

  await page.evaluate(() => { (window as typeof window & { __failNextVideoEstimate: boolean }).__failNextVideoEstimate = true; });
  await page.locator("#vg-prompt").fill("估价失败必须阻断");
  await page.getByRole("button", { name: "生成视频" }).click();
  await expect(page.getByText("暂时无法获取价格，请稍后重试")).toBeVisible();
  await expect(page.getByRole("button", { name: "确定", exact: true })).toBeDisabled();
  expect((await page.evaluate(() => (window as typeof window & { __pricingProbe: Probe }).__pricingProbe)).videoSubmits).toBe(0);
  await page.getByRole("button", { name: "取消" }).click();

  const deferred = await page.evaluate(async () => {
    const response = await fetch("http://localhost:8000/api/v1/videos/estimate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_mode: "static_template", topic: "延期模板", duration_sec: 5, resolution: "720p" })
    });
    return response.json();
  });
  expect(deferred.data).toMatchObject({ pricing_contract: "deferred_unpriced", unpriced: true, note: "延期处理／尚未闭环" });

  await page.getByRole("button", { name: "数字人口播" }).click();
  await page.locator("#video-topic").fill("品牌音色视频");
  await page.getByText("默认主播", { exact: true }).click();
  await page.getByText("免费复刻音", { exact: true }).click();
  await page.getByRole("button", { name: "生成视频" }).click();
  await expect(page.getByText("使用品牌音色前请先生成或填写口播文案", { exact: true })).toBeVisible();
  await expect(page.locator("#video-script")).toBeFocused();

  await page.locator("#video-script").fill("这是一段需要按字符计费的品牌口播文案");
  await page.getByRole("button", { name: "生成视频" }).click();
  await expect(page.getByText("CosyVoice 品牌音色")).toBeVisible();
  await expect(page.getByText(/0\.1000 积分 \/ character/)).toBeVisible();
  expect(errors).toEqual([]);
});

test("ecom batch accepts 20, rejects 21 and explains partial settlement", async ({ page }) => {
  test.setTimeout(120_000);
  const errors = watchErrors(page);
  await installBillingProbe(page);
  await login(page);
  await page.getByRole("button", { name: "电商图" }).click();
  await page.getByRole("button", { name: "批量", exact: true }).click();

  const files = Array.from({ length: 20 }, (_, index) => ({
    name: `item-${index + 1}.png`, mimeType: "image/png", buffer: Buffer.from([index + 1])
  }));
  await page.locator("#ecom-cutout-batch").setInputFiles(files);
  await expect(page.getByRole("button", { name: "移除图片" })).toHaveCount(20, { timeout: 30_000 });
  await page.locator("#ecom-cutout-batch").setInputFiles({ name: "item-21.png", mimeType: "image/png", buffer: Buffer.from([21]) });
  await expect(page.getByText("每批最多 20 张图片", { exact: true })).toBeVisible();

  for (let index = 0; index < 20; index += 1) await page.getByRole("button", { name: "移除图片" }).first().click();
  await page.locator("#ecom-cutout-batch").setInputFiles([
    { name: "success.png", mimeType: "image/png", buffer: Buffer.from("success") },
    { name: "__FAIL__.png", mimeType: "image/png", buffer: Buffer.from("failure") }
  ]);
  await expect(page.getByRole("button", { name: "移除图片" })).toHaveCount(2, { timeout: 15_000 });
  await page.getByRole("button", { name: "生成", exact: true }).click();
  await expect(page.getByText("160 积分").last()).toBeVisible();
  await page.getByRole("button", { name: "确认并继续" }).click();
  await expect(page.getByText("部分结算 80 积分，已释放 80 积分")).toBeVisible();
  await expect(page.getByText("仅结算成功生成的图片，失败图片对应的冻结积分已释放。")).toBeVisible();
  expect(errors).toEqual([]);
});
