import { expect, test, type Page } from "@playwright/test";

type Probe = {
  scriptSubmits: number;
  lookups: number;
  videoSubmits: number;
  estimateQuotes: string[];
  posts: Array<{
    path: string;
    body: Record<string, unknown> | null;
    status: number;
    data: Record<string, unknown> | null;
    quote: string | null;
    key: string | null;
  }>;
};

type ExpectedFault = {
  method: "POST";
  url: string;
  status: number;
};

async function installBillingProbe(page: Page) {
  const observedPosts: Probe["posts"] = [];
  page.on("response", async (response) => {
    const request = response.request();
    const path = new URL(response.url()).pathname;
    if (request.method() !== "POST" || !(
      path === "/api/v1/scripts/generate" ||
      path === "/api/v1/videos" ||
      path.includes("/api/v1/ecom-images/cutout")
    )) return;
    const payload = await response.json().catch(() => null) as { data?: Record<string, unknown> | null } | null;
    observedPosts.push({
      path,
      body: null,
      status: response.status(),
      data: payload?.data ?? null,
      quote: await request.headerValue("X-Huading-Quote"),
      key: await request.headerValue("Idempotency-Key")
    });
  });
  await page.evaluate(() => {
    const target = window as typeof window & {
      __pricingProbe?: Probe;
      __dropNextScript?: boolean;
      __failNextVideoEstimate?: boolean;
    };
    target.__pricingProbe = { scriptSubmits: 0, lookups: 0, videoSubmits: 0, estimateQuotes: [], posts: [] };
    const original = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init);
      const url = new URL(request.url, location.href);
      const method = request.method.toUpperCase();
      const isScriptSubmit = method === "POST" && url.pathname.endsWith("/api/v1/scripts/generate");
      const isLookup = method === "GET" && url.pathname.includes("/api/v1/billing/operations/by-idempotency/");
      const isVideoSubmit = method === "POST" && url.pathname.endsWith("/api/v1/videos");
      const shouldRecord =
        isScriptSubmit ||
        isVideoSubmit ||
        (method === "POST" && url.pathname.includes("/api/v1/ecom-images/cutout"));
      const recordedBody = shouldRecord
        ? await request.clone().json().catch(() => ({})) as Record<string, unknown>
        : null;
      let failedUpload = false;
      if (method === "POST" && url.pathname.endsWith("/api/v1/uploads/images")) {
        const form = await request.clone().formData().catch(() => null);
        const file = form?.get("file");
        failedUpload = file instanceof File && file.name.includes("__FAIL__");
      }
      if (isScriptSubmit) target.__pricingProbe!.scriptSubmits += 1;
      if (isVideoSubmit) target.__pricingProbe!.videoSubmits += 1;
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
      if (shouldRecord && recordedBody) {
        const payload = await response.clone().json().catch(() => null) as { data?: Record<string, unknown> | null } | null;
        target.__pricingProbe!.posts.push({
          path: url.pathname,
          body: recordedBody,
          status: response.status,
          data: payload?.data ?? null,
          quote: request.headers.get("X-Huading-Quote"),
          key: request.headers.get("Idempotency-Key")
        });
      }
      if (isScriptSubmit && target.__dropNextScript) {
        target.__dropNextScript = false;
        throw new TypeError("Failed to fetch after supplier completion");
      }
      return response;
    };
  });
  return observedPosts;
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
  let expectedFault: (ExpectedFault & { observed: boolean; consoleBudget: number; requestFailureBudget: number }) | null = null;
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (
      expectedFault &&
      !expectedFault.observed &&
      response.request().method() === expectedFault.method &&
      response.url() === expectedFault.url &&
      response.status() === expectedFault.status
    ) {
      expectedFault.observed = true;
      expectedFault.consoleBudget = 1;
    }
  });
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    const locationUrl = message.location().url;
    const endpointMatches = expectedFault && (locationUrl === expectedFault.url || text.includes(expectedFault.url));
    const isExpectedStatus = expectedFault && endpointMatches && (
      text.includes(`status of ${expectedFault.status}`) ||
      text.includes(`${expectedFault.status} (`)
    );
    if (expectedFault?.observed && expectedFault.consoleBudget > 0 && isExpectedStatus) {
      expectedFault.consoleBudget -= 1;
      return;
    }
    errors.push(text);
  });
  page.on("requestfailed", (request) => {
    if (
      expectedFault?.observed &&
      expectedFault.requestFailureBudget > 0 &&
      request.method() === expectedFault.method &&
      request.url() === expectedFault.url
    ) {
      expectedFault.requestFailureBudget -= 1;
      return;
    }
    errors.push(`requestfailed ${request.method()} ${new URL(request.url()).pathname}: ${request.failure()?.errorText ?? "unknown"}`);
  });
  return {
    errors,
    begin(fault: ExpectedFault) {
      expect(expectedFault).toBeNull();
      expectedFault = { ...fault, observed: false, consoleBudget: 0, requestFailureBudget: 1 };
    },
    end() {
      expect(expectedFault?.observed).toBe(true);
      expectedFault = null;
    }
  };
}

test("mock MP4 boundary provides decodable video metadata", async ({ page }) => {
  const mediaResponses: Array<{ status: number; contentType: string | undefined }> = [];
  const mediaFailures: string[] = [];
  page.on("response", (response) => {
    if (!response.url().includes("mock-v2v-1080p-2s.mp4")) return;
    mediaResponses.push({
      status: response.status(),
      contentType: response.headers()["content-type"]
    });
  });
  page.on("requestfailed", (request) => {
    if (/\.mp4(?:\?|$)/i.test(request.url())) {
      mediaFailures.push(`${request.url()}: ${request.failure()?.errorText ?? "unknown failure"}`);
    }
  });

  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller);
  const metadata = await page.evaluate(() => new Promise<{
    duration: number;
    readyState: number;
    videoHeight: number;
    videoWidth: number;
  }>((resolve, reject) => {
    const video = document.createElement("video");
    video.preload = "metadata";
    video.muted = true;
    video.addEventListener("loadedmetadata", () => resolve({
      duration: video.duration,
      readyState: video.readyState,
      videoHeight: video.videoHeight,
      videoWidth: video.videoWidth
    }), { once: true });
    video.addEventListener("error", () => reject(new Error(
      `Mock MP4 failed to load metadata (MediaError ${video.error?.code ?? "unknown"}).`
    )), { once: true });
    video.src = "https://mock.local/v.mp4";
    document.body.append(video);
    video.load();
  }));

  expect(Number.isFinite(metadata.duration)).toBe(true);
  expect(metadata.duration).toBeGreaterThan(0);
  expect(metadata.readyState).toBeGreaterThanOrEqual(1);
  expect(metadata.videoWidth).toBeGreaterThan(0);
  expect(metadata.videoHeight).toBeGreaterThan(0);
  expect(mediaResponses).toContainEqual({
    status: expect.any(Number),
    contentType: expect.stringMatching(/^video\/mp4(?:;|$)/i)
  });
  expect(mediaResponses.every(({ status }) => status === 200 || status === 206)).toBe(true);
  expect(mediaFailures).toEqual([]);
});

test("unknown script result recovers once, preserves billing headers and invalidates edited quotes", async ({ page }) => {
  const errorWatch = watchErrors(page);
  await login(page);
  const observedPosts = await installBillingProbe(page);

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
  errorWatch.begin({ method: "POST", url: "http://localhost:8000/api/v1/scripts/generate", status: 200 });
  await page.evaluate(() => console.error("Failed to fetch after supplier completion"));
  await expect.poll(() => errorWatch.errors.filter((error) => error.includes("Failed to fetch after supplier completion")).length).toBe(1);
  errorWatch.errors.splice(errorWatch.errors.findIndex((error) => error.includes("Failed to fetch after supplier completion")), 1);
  await page.getByRole("button", { name: "确认并继续" }).click();
  await expect(page.getByText("计费结果确认中")).toBeVisible();
  for (const disclaimer of ["未扣费", "未扣款", "未收费", "不会扣费", "不扣费"]) {
    await expect(page.getByText(disclaimer, { exact: false })).toHaveCount(0);
  }
  await expect(page.getByText("已结算 1 积分")).toBeVisible();
  await page.evaluate(() => console.error("Failed to fetch after supplier completion"));
  await expect.poll(() => errorWatch.errors.filter((error) => error.includes("Failed to fetch after supplier completion")).length).toBe(1);
  errorWatch.errors.splice(errorWatch.errors.findIndex((error) => error.includes("Failed to fetch after supplier completion")), 1);
  errorWatch.end();
  await expect(page.locator("#video-script")).toContainText("编辑后的定价主题");

  const probe = await page.evaluate(() => (window as typeof window & { __pricingProbe: Probe }).__pricingProbe);
  expect(probe.scriptSubmits).toBe(1);
  expect(probe.lookups).toBeGreaterThanOrEqual(1);
  await expect.poll(() => observedPosts.filter((post) => post.path === "/api/v1/scripts/generate").length).toBe(1);
  const scriptPost = observedPosts.filter((post) => post.path === "/api/v1/scripts/generate");
  expect(scriptPost).toHaveLength(1);
  expect(scriptPost[0].quote).toBe(secondQuote);
  expect(scriptPost[0].key).toMatch(/^[0-9a-f-]{36}$/i);
  expect(errorWatch.errors).toEqual([]);
});

test("video contracts expose legacy, deferred and CosyVoice billing while estimate failure stays closed", async ({ page }) => {
  const errorWatch = watchErrors(page);
  await login(page);
  const observedPosts = await installBillingProbe(page);

  await page.getByRole("button", { name: "视频生成", exact: true }).click();
  await page.locator("#vg-prompt").fill("普通视频估价");
  await page.getByRole("button", { name: "生成视频" }).click();
  await expect(page.getByText(/预计消耗\s*2\s*积分/)).toBeVisible();
  await page.getByRole("button", { name: "确定", exact: true }).click();
  await expect(page.getByText("普通视频估价", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/排队中|生成中|已完成/).first()).toBeVisible();

  await expect.poll(() => observedPosts.filter((post) => post.path === "/api/v1/videos").length).toBe(1);
  let videoPosts = observedPosts.filter((post) => post.path === "/api/v1/videos");
  expect(videoPosts).toHaveLength(1);
  expect(videoPosts[0]).toMatchObject({
    status: 202,
    data: { pricing_contract: "legacy_estimate", status: "queued" },
    quote: null,
    key: null
  });
  expect(videoPosts[0].data?.id).toBe(videoPosts[0].data?.task_id);
  expect(videoPosts[0].data).not.toHaveProperty("billing");

  await page.evaluate(() => { (window as typeof window & { __failNextVideoEstimate: boolean }).__failNextVideoEstimate = true; });
  await page.locator("#vg-prompt").fill("估价失败必须阻断");
  await page.getByRole("button", { name: "生成视频" }).click();
  await expect(page.getByText("暂时无法获取价格，请稍后重试")).toBeVisible();
  await expect(page.getByRole("button", { name: "确定", exact: true })).toBeDisabled();
  expect(observedPosts.filter((post) => post.path === "/api/v1/videos")).toHaveLength(1);
  await page.getByRole("button", { name: "取消" }).click();

  const deferred = await page.evaluate(async () => {
    const request = { video_mode: "static_template", topic: "延期模板", duration_sec: 5, resolution: "720p" };
    const estimateResponse = await fetch("http://localhost:8000/api/v1/videos/estimate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request)
    });
    const submitResponse = await fetch("http://localhost:8000/api/v1/videos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request)
    });
    return {
      estimateStatus: estimateResponse.status,
      estimate: await estimateResponse.json(),
      submitStatus: submitResponse.status,
      accepted: await submitResponse.json()
    };
  });
  expect(deferred.estimateStatus).toBe(200);
  expect(deferred.estimate.data).toMatchObject({ pricing_contract: "deferred_unpriced", unpriced: true, note: "延期处理／尚未闭环" });
  expect(deferred.submitStatus).toBe(202);
  expect(deferred.accepted.data).toMatchObject({ pricing_contract: "deferred_unpriced", status: "queued" });
  expect(deferred.accepted.data.id).toBe(deferred.accepted.data.task_id);
  expect(deferred.accepted.data).not.toHaveProperty("billing");

  await page.getByRole("button", { name: "数字人口播" }).click();
  await page.locator("#video-topic").fill("品牌音色视频");
  await page.getByRole("button", { name: "默认主播" }).click();
  await page.getByRole("button", { name: /免费复刻音/ }).click();
  errorWatch.begin({ method: "POST", url: "http://localhost:8000/api/v1/videos/estimate", status: 422 });
  await page.getByRole("button", { name: "生成视频" }).click();
  await expect(page.getByText("使用品牌音色前请先生成或填写口播文案", { exact: true })).toBeVisible();
  errorWatch.end();
  await expect(page.locator("#video-script")).toBeFocused();

  await page.locator("#video-script").fill("这是一段需要按字符计费的品牌口播文案");
  await page.getByRole("button", { name: "生成视频" }).click();
  await expect(page.getByText("CosyVoice 品牌音色")).toBeVisible();
  await expect(page.getByText(/0\.1000 积分 \/ character/)).toBeVisible();
  await page.getByRole("button", { name: "确认并继续" }).click();
  await expect(page.getByText(/已冻结 \d+ 积分/)).toBeVisible();
  await expect(page.getByText("品牌音色视频", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/排队中|生成中|已完成/).first()).toBeVisible();

  await expect.poll(() => observedPosts.filter((post) => post.path === "/api/v1/videos").length).toBe(3);
  videoPosts = observedPosts.filter((post) => post.path === "/api/v1/videos");
  expect(videoPosts).toHaveLength(3);
  const billingPost = videoPosts.find((post) => post.data?.pricing_contract === "billing_quote");
  expect(billingPost).toMatchObject({
    status: 202,
    data: {
      pricing_contract: "billing_quote",
      status: "queued",
      billing: { status: "reserved", settled_credits: 0, released_credits: 0 }
    }
  });
  const billing = billingPost?.data?.billing as { requested_credits: number; held_credits: number };
  expect(billing.requested_credits).toBeGreaterThan(0);
  expect(billing.held_credits).toBe(billing.requested_credits);
  expect(billingPost?.data?.id).toBe(billingPost?.data?.task_id);
  expect(billingPost?.quote).toBeTruthy();
  expect(billingPost?.key).toMatch(/^[0-9a-f-]{36}$/i);
  expect(errorWatch.errors).toEqual([]);
});

test("ecom batch accepts 20, rejects 21 and explains partial settlement", async ({ page }) => {
  test.setTimeout(120_000);
  const errorWatch = watchErrors(page);
  await login(page);
  const observedPosts = await installBillingProbe(page);
  await page.getByRole("button", { name: "电商图" }).click();
  await page.getByRole("button", { name: "批量", exact: true }).click();

  const files = Array.from({ length: 20 }, (_, index) => ({
    name: `item-${index + 1}.png`, mimeType: "image/png", buffer: Buffer.from([index + 1])
  }));
  await page.locator("#ecom-cutout-batch").setInputFiles(files);
  await expect(page.getByRole("button", { name: "移除图片" })).toHaveCount(20, { timeout: 30_000 });
  await page.locator("#ecom-cutout-batch").setInputFiles({ name: "item-21.png", mimeType: "image/png", buffer: Buffer.from([21]) });
  await expect(page.getByText("每批最多 20 张图片", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "生成", exact: true }).click();
  await expect(page.getByText("1600 积分").last()).toBeVisible();
  await page.getByRole("button", { name: "确认并继续" }).click();
  await expect(page.getByText("已结算 1600 积分")).toBeVisible();

  await expect.poll(() => observedPosts.filter((post) => post.path.endsWith("/cutout/batch")).length).toBe(1);
  const twentyEstimate = observedPosts.find((post) => post.path.endsWith("/cutout/estimate") && post.status === 200);
  const twentySubmit = observedPosts.find((post) => post.path.endsWith("/cutout/batch") && post.status === 200);
  expect(twentyEstimate).toMatchObject({ status: 200, data: { payable_credits: 1600, quantity: "20" } });
  expect(twentySubmit).toMatchObject({ status: 200, data: { tasks: expect.any(Array) } });
  expect((twentySubmit?.data?.tasks as unknown[])).toHaveLength(20);
  expect(twentySubmit?.quote).toBe(twentyEstimate?.data?.quote_token);
  expect(twentySubmit?.key).toMatch(/^[0-9a-f-]{36}$/i);

  const validItem = { source_asset_id: "upload-21-boundary", background: "white", aspect_ratio: "1:1", apply_visible_label: false };
  const invalidBoundary = await page.evaluate(async (item) => {
    const before = await fetch("http://localhost:8000/api/v1/videos?mode=photo").then((response) => response.json());
    const body = { items: Array.from({ length: 21 }, () => ({ ...item })) };
    return { before: before.data.total, body };
  }, validItem);
  const supplierInvocationsBeforeInvalid = await page.evaluate(async () => {
    const response = await fetch("http://localhost:8000/api/v1/__mock__/supplier-invocations");
    return response.json().then((payload) => payload.data.ecom_cutout as number);
  });
  errorWatch.begin({ method: "POST", url: "http://localhost:8000/api/v1/ecom-images/cutout/estimate", status: 422 });
  const invalidEstimate = await page.evaluate(async (body) => {
    const response = await fetch("http://localhost:8000/api/v1/ecom-images/cutout/estimate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    return { status: response.status, payload: await response.json() };
  }, invalidBoundary.body);
  errorWatch.end();
  errorWatch.begin({ method: "POST", url: "http://localhost:8000/api/v1/ecom-images/cutout/batch", status: 422 });
  const invalidSubmit = await page.evaluate(async (body) => {
    const response = await fetch("http://localhost:8000/api/v1/ecom-images/cutout/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    return { status: response.status, payload: await response.json() };
  }, invalidBoundary.body);
  errorWatch.end();
  const afterInvalid = await page.evaluate(async () => {
    const [tasksResponse, supplierResponse] = await Promise.all([
      fetch("http://localhost:8000/api/v1/videos?mode=photo"),
      fetch("http://localhost:8000/api/v1/__mock__/supplier-invocations")
    ]);
    const [tasks, supplier] = await Promise.all([tasksResponse.json(), supplierResponse.json()]);
    return { tasks, supplierInvocations: supplier.data.ecom_cutout as number };
  });
  expect(invalidEstimate).toMatchObject({ status: 422, payload: { data: null, error: { code: "VALIDATION_ERROR" } } });
  expect(invalidSubmit).toMatchObject({ status: 422, payload: { data: null, error: { code: "VALIDATION_ERROR" } } });
  expect(invalidSubmit.payload.data).not.toEqual(expect.objectContaining({ tasks: expect.any(Array) }));
  expect(afterInvalid.tasks.data.total).toBe(invalidBoundary.before);
  expect(afterInvalid.supplierInvocations).toBe(supplierInvocationsBeforeInvalid);

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
  expect(errorWatch.errors).toEqual([]);
});
