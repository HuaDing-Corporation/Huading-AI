import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getMockSupplierInvocations, resetMockSupplierInvocations } from "@/mocks/handlers";
import { server } from "@/mocks/server";
import { getBillingOperation } from "./billing";
import type {
  CutoutBatchRequest,
  CutoutBatchResponse,
  CutoutRequest,
  CutoutResponse,
  ModelBatchRequest,
  ModelBatchResponse,
  ModelRequest,
  ModelResponse
} from "./types";

import {
  cutoutImage,
  createEcomCutout,
  createEcomModel,
  estimateEcomCutout,
  estimateEcomModel,
  listModelStyles,
  listPosterTemplates,
  posterImage,
  posterImageBatch
} from "./ecom-images";
import { getVideo, streamVideoEvents } from "./videos";

const API = "http://localhost:8000";

function ecomQuote(operation: "ecom_cutout" | "ecom_model", quantity = 1) {
  return {
    pricing_contract: "billing_quote" as const,
    pricing_shape: "simple" as const,
    operation,
    unit: "image",
    quantity: String(quantity),
    unit_credits: "80",
    subtotal_credits: String(80 * quantity),
    payable_credits: 80 * quantity,
    rate_scope: "tenant_overridable" as const,
    rate_source: "platform_rate" as const,
    breakdown: [] as [],
    disclosures: [],
    quote_token: `${operation}-quote`,
    expires_at: new Date(Date.now() + 60_000).toISOString()
  };
}

let confirmedRequestSequence = 0;

beforeEach(() => resetMockSupplierInvocations());

function nextConfirmation(quoteToken: string) {
  confirmedRequestSequence += 1;
  return {
    quote_token: quoteToken,
    idempotency_key: `10000000-0000-4000-8000-${String(confirmedRequestSequence).padStart(12, "0")}`
  };
}

async function confirmedCutout(body: CutoutRequest): Promise<CutoutResponse>;
async function confirmedCutout(body: CutoutBatchRequest): Promise<CutoutBatchResponse>;
async function confirmedCutout(body: CutoutRequest | CutoutBatchRequest) {
  const quote = await estimateEcomCutout(body);
  return "items" in body
    ? createEcomCutout(body, nextConfirmation(quote.quote_token))
    : createEcomCutout(body, nextConfirmation(quote.quote_token));
}

async function confirmedModel(body: ModelRequest): Promise<ModelResponse>;
async function confirmedModel(body: ModelBatchRequest): Promise<ModelBatchResponse>;
async function confirmedModel(body: ModelRequest | ModelBatchRequest) {
  const quote = await estimateEcomModel(body);
  return "items" in body
    ? createEcomModel(body, nextConfirmation(quote.quote_token))
    : createEcomModel(body, nextConfirmation(quote.quote_token));
}

describe("ecom-images authoritative billing API", () => {
  it("routes one cutout body through estimate and submit with the exact confirmation headers", async () => {
    const calls = vi.fn();
    const input = { source_asset_id: "asset-1", background: "white" as const };
    server.use(
      http.post(`${API}/api/v1/ecom-images/cutout/estimate`, async ({ request }) => {
        calls("estimate", await request.json());
        return HttpResponse.json({ data: ecomQuote("ecom_cutout"), error: null, request_id: "estimate" });
      }),
      http.post(`${API}/api/v1/ecom-images/cutout`, async ({ request }) => {
        calls("submit", {
          body: await request.json(),
          quote: request.headers.get("X-Huading-Quote"),
          key: request.headers.get("Idempotency-Key")
        });
        return HttpResponse.json({
          data: { task_id: "task-1", status: "queued" },
          error: null,
          request_id: "submit"
        });
      })
    );

    await expect(estimateEcomCutout(input)).resolves.toMatchObject({
      operation: "ecom_cutout",
      payable_credits: 80
    });
    await expect(
      createEcomCutout(input, {
        quote_token: "ecom_cutout-quote",
        idempotency_key: "11111111-1111-4111-8111-111111111111"
      })
    ).resolves.toEqual({ task_id: "task-1", status: "queued" });
    expect(calls.mock.calls).toEqual([
      ["estimate", input],
      [
        "submit",
        {
          body: input,
          quote: "ecom_cutout-quote",
          key: "11111111-1111-4111-8111-111111111111"
        }
      ]
    ]);
  });

  it("routes a cutout batch body through the batch submit endpoint without changing its items", async () => {
    const seen = vi.fn();
    const input = {
      items: [
        { source_asset_id: "asset-1", background: "white" as const },
        { source_asset_id: "asset-2", background: "transparent" as const }
      ]
    };
    server.use(
      http.post(`${API}/api/v1/ecom-images/cutout/estimate`, async ({ request }) => {
        seen("estimate", await request.json());
        return HttpResponse.json({ data: ecomQuote("ecom_cutout", 2), error: null, request_id: "estimate" });
      }),
      http.post(`${API}/api/v1/ecom-images/cutout/batch`, async ({ request }) => {
        seen("submit", {
          body: await request.json(),
          quote: request.headers.get("X-Huading-Quote"),
          key: request.headers.get("Idempotency-Key")
        });
        return HttpResponse.json({
          data: {
            batch_id: "batch-1",
            tasks: input.items.map((item, index) => ({
              task_id: `task-${index}`,
              source_asset_id: item.source_asset_id,
              status: "queued"
            }))
          },
          error: null,
          request_id: "submit"
        });
      })
    );

    await expect(estimateEcomCutout(input)).resolves.toMatchObject({
      quantity: "2",
      payable_credits: 160
    });
    await expect(
      createEcomCutout(input, {
        quote_token: "ecom_cutout-quote",
        idempotency_key: "22222222-2222-4222-8222-222222222222"
      })
    ).resolves.toMatchObject({ batch_id: "batch-1", tasks: [{ task_id: "task-0" }, { task_id: "task-1" }] });
    expect(seen.mock.calls).toEqual([
      ["estimate", input],
      [
        "submit",
        {
          body: input,
          quote: "ecom_cutout-quote",
          key: "22222222-2222-4222-8222-222222222222"
        }
      ]
    ]);
  });

  it("rejects 21 cutout items before any supplier invocation", async () => {
    await confirmedCutout({ source_asset_id: "supplier-baseline", background: "white" });
    const beforeInvalid = getMockSupplierInvocations().ecom_cutout;
    expect(beforeInvalid).toBe(1);
    const invalid = {
      items: Array.from({ length: 21 }, (_, index) => ({
        source_asset_id: `asset-${index + 1}`,
        background: "white" as const
      }))
    };

    await expect(estimateEcomCutout(invalid)).rejects.toMatchObject({ status: 422, code: "VALIDATION_ERROR" });
    await expect(createEcomCutout(invalid, nextConfirmation("invalid-quote"))).rejects.toMatchObject({ status: 422, code: "VALIDATION_ERROR" });
    expect(getMockSupplierInvocations().ecom_cutout).toBe(beforeInvalid);
  });

  it("routes one model request through estimate and submit with the same body and confirmation", async () => {
    const seen = vi.fn();
    const input = {
      product_asset_ids: ["product-1", "product-2"],
      model_asset_ids: ["model-1"],
      product_images_mode: "multi_item" as const,
      gender: "female" as const,
      custom_style: "自然棚拍"
    };
    server.use(
      http.post(`${API}/api/v1/ecom-images/model/estimate`, async ({ request }) => {
        seen("estimate", await request.json());
        return HttpResponse.json({ data: ecomQuote("ecom_model"), error: null, request_id: "estimate" });
      }),
      http.post(`${API}/api/v1/ecom-images/model`, async ({ request }) => {
        seen("submit", {
          body: await request.json(),
          quote: request.headers.get("X-Huading-Quote"),
          key: request.headers.get("Idempotency-Key")
        });
        return HttpResponse.json({
          data: { task_id: "model-task-1", status: "queued" },
          error: null,
          request_id: "submit"
        });
      })
    );

    await expect(estimateEcomModel(input)).resolves.toMatchObject({ operation: "ecom_model" });
    await expect(
      createEcomModel(input, {
        quote_token: "ecom_model-quote",
        idempotency_key: "33333333-3333-4333-8333-333333333333"
      })
    ).resolves.toEqual({ task_id: "model-task-1", status: "queued" });
    expect(seen.mock.calls).toEqual([
      ["estimate", input],
      [
        "submit",
        {
          body: input,
          quote: "ecom_model-quote",
          key: "33333333-3333-4333-8333-333333333333"
        }
      ]
    ]);
  });

  it("routes a model batch to the batch endpoint and preserves the complete request", async () => {
    const seen = vi.fn();
    const input = {
      items: [
        { source_asset_id: "asset-1", gender: "female" as const, style_id: "studio_white" },
        { source_asset_id: "asset-2", gender: "male" as const, style_id: "street" }
      ]
    };
    server.use(
      http.post(`${API}/api/v1/ecom-images/model/estimate`, async ({ request }) => {
        seen("estimate", await request.json());
        return HttpResponse.json({ data: ecomQuote("ecom_model", 2), error: null, request_id: "estimate" });
      }),
      http.post(`${API}/api/v1/ecom-images/model/batch`, async ({ request }) => {
        seen("submit", {
          body: await request.json(),
          quote: request.headers.get("X-Huading-Quote"),
          key: request.headers.get("Idempotency-Key")
        });
        return HttpResponse.json({
          data: {
            batch_id: "model-batch-1",
            tasks: input.items.map((item, index) => ({
              task_id: `model-task-${index}`,
              source_asset_id: item.source_asset_id,
              status: "queued"
            }))
          },
          error: null,
          request_id: "submit"
        });
      })
    );

    await expect(estimateEcomModel(input)).resolves.toMatchObject({ quantity: "2" });
    await expect(
      createEcomModel(input, {
        quote_token: "ecom_model-quote",
        idempotency_key: "44444444-4444-4444-8444-444444444444"
      })
    ).resolves.toMatchObject({
      batch_id: "model-batch-1",
      tasks: [{ task_id: "model-task-0" }, { task_id: "model-task-1" }]
    });
    expect(seen.mock.calls).toEqual([
      ["estimate", input],
      [
        "submit",
        {
          body: input,
          quote: "ecom_model-quote",
          key: "44444444-4444-4444-8444-444444444444"
        }
      ]
    ]);
  });

  it("default MSW rejects a priced submit with no confirmation headers", async () => {
    await expect(
      cutoutImage({ source_asset_id: "asset-no-confirmation", background: "white" })
    ).rejects.toMatchObject({ code: "BILLING_HEADERS_REQUIRED", status: 422 });
  });

  it("binds every batch item so changing only the twentieth item invalidates the quote", async () => {
    const items = Array.from({ length: 20 }, (_, index) => ({
      source_asset_id: `asset-${index}`,
      background: "white" as const
    }));
    const quote = await estimateEcomCutout({ items });
    const changed = {
      items: items.map((item, index) =>
        index === 19 ? { ...item, source_asset_id: "asset-replaced-19" } : item
      )
    };

    await expect(
      createEcomCutout(changed, {
        quote_token: quote.quote_token,
        idempotency_key: "77777777-7777-4777-8777-777777777777"
      })
    ).rejects.toMatchObject({ code: "PRICE_CHANGED", status: 409 });
  });

  it("stores an idempotent partial batch operation and exposes its typed lookup", async () => {
    const input = {
      items: [
        { source_asset_id: "asset-success", background: "white" as const },
        { source_asset_id: "asset-__FAIL__", background: "white" as const }
      ]
    };
    const key = "88888888-8888-4888-8888-888888888888";
    const quote = await estimateEcomCutout(input);
    const confirmation = { quote_token: quote.quote_token, idempotency_key: key };

    const first = await createEcomCutout(input, confirmation);
    const replay = await createEcomCutout(input, confirmation);
    expect(replay).toEqual(first);

    const lookup = await getBillingOperation("ecom_cutout", key);
    expect(lookup).toMatchObject({
      operation: "ecom_cutout",
      idempotency_key: key,
      state: "completed",
      completion_kind: "succeeded",
      result_type: "ecom_image_batch",
      billing: {
        status: "partially_settled",
        requested_credits: 160,
        settled_credits: 80,
        released_credits: 80
      }
    });
    if (
      lookup.state !== "completed" ||
      lookup.completion_kind !== "succeeded" ||
      lookup.result_type !== "ecom_image_batch"
    ) {
      throw new Error("expected completed e-commerce lookup");
    }
    expect(lookup.result.items.map((item) => item.status)).toEqual(["done", "failed"]);
  });

  it.each([
    ["asset-__QUOTE_EXPIRED__", "QUOTE_EXPIRED"],
    ["asset-__PRICE_CHANGED__", "PRICE_CHANGED"]
  ])("provides the %s fixture and fails closed with %s", async (sourceAssetId, code) => {
    const input = { source_asset_id: sourceAssetId, background: "white" as const };
    const quote = await estimateEcomCutout(input);
    await expect(
      createEcomCutout(input, {
        quote_token: quote.quote_token,
        idempotency_key:
          code === "QUOTE_EXPIRED"
            ? "99999999-9999-4999-8999-999999999999"
            : "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
      })
    ).rejects.toMatchObject({ code, status: 409 });
  });
});

// 集成测试：不 mock hooks/handlers，真 apiFetch → MSW(vitest.setup 已 server.listen)。
// 验证 mock 忠实(吸取教训)：塞真 photo task(kind=ecom_cutout)、N clamp、SSE 不覆盖产物 URL。

describe("ecom-images API ↔ MSW（mock 忠实）", () => {
  it("单张 cutout：塞真 photo task(kind=ecom_cutout, done, 白底图 url)，GET /videos/:id 轮询拿到", async () => {
    const res = await confirmedCutout({ source_asset_id: "a1", background: "white" });
    expect(res.task_id).toBeTruthy();
    const v = await getVideo(res.task_id);
    expect(v.status).toBe("done");
    expect((v as { kind?: string }).kind).toBe("ecom_cutout");
    expect(v.playback_url).toContain("cutout-white");
  });

  it("透明底 cutout：产物 url 区分(cutout-transparent)", async () => {
    const res = await confirmedCutout({ source_asset_id: "a1", background: "transparent" });
    const v = await getVideo(res.task_id);
    expect(v.playback_url).toContain("cutout-transparent");
  });

  it("SSE 轮询后产物 URL 不被通用 v.mp4 覆盖(#1 回归：预 seeded 终态保留)", async () => {
    const res = await confirmedCutout({ source_asset_id: "a1", background: "white" });
    // 模拟 trackExisting → subscribe：消费整段 SSE 流(running→done)
    await streamVideoEvents(res.task_id, () => undefined);
    const v = await getVideo(res.task_id);
    expect(v.playback_url).toContain("cutout-white");
    expect(v.playback_url).not.toContain("v.mp4");
  });

  it("批量 cutout：第 21 项使整批 422，不静默截断", async () => {
    const items = Array.from({ length: 21 }, (_, i) => ({ source_asset_id: `a${i}`, background: "white" as const }));
    await expect(estimateEcomCutout({ items })).rejects.toMatchObject({ status: 422 });
  });

  it("批量 cutout：fan-out 各 task 可轮询到 done + cutout 产物", async () => {
    const res = await confirmedCutout({
      items: [
        { source_asset_id: "a1", background: "white" },
        { source_asset_id: "a2", background: "transparent" }
      ]
    });
    expect(res.tasks).toHaveLength(2);
    const v0 = await getVideo(res.tasks[0].task_id);
    expect(v0.status).toBe("done");
    expect(v0.playback_url).toContain("cutout");
  });
});

describe("ecom-images model API ↔ MSW（mock 忠实，Phase2 AI 模特）", () => {
  it("model-styles：返回非空风格预设列表(含 id/name)", async () => {
    const styles = await listModelStyles();
    expect(styles.length).toBeGreaterThan(0);
    expect(styles[0]).toHaveProperty("id");
    expect(styles[0]).toHaveProperty("name");
  });

  it("单张 model：多商品图 + 组合语义 → 塞真 photo task(kind=ecom_model, done)，GET /videos/:id 轮询拿到", async () => {
    const res = await confirmedModel({ product_asset_ids: ["a1", "a2"], product_images_mode: "multi_item", gender: "female", style_id: "studio_white" });
    expect(res.task_id).toBeTruthy();
    const v = await getVideo(res.task_id);
    expect(v.status).toBe("done");
    expect((v as { kind?: string }).kind).toBe("ecom_model");
    expect(v.playback_url).toContain("model-");
  });

  it("单张 model：模特图 + 自定义风格 → done（custom 产物 url）", async () => {
    const res = await confirmedModel({ product_asset_ids: ["a1"], model_asset_ids: ["m1"], product_images_mode: "multi_angle", gender: "any", custom_style: "赛博朋克霓虹" });
    await streamVideoEvents(res.task_id, () => undefined);
    const v = await getVideo(res.task_id);
    expect(v.playback_url).toContain("model-");
    expect(v.playback_url).not.toContain("v.mp4");
  });

  it("批量 model：第 21 项使整批 422，不静默截断", async () => {
    const items = Array.from({ length: 21 }, (_, i) => ({ source_asset_id: `a${i}`, gender: "female" as const, style_id: "studio" }));
    await expect(estimateEcomModel({ items })).rejects.toMatchObject({ status: 422 });
  });

  it("批量 model：fan-out 各 task 可轮询到 done + 模特产物", async () => {
    const res = await confirmedModel({
      items: [
        { source_asset_id: "a1", gender: "female", style_id: "studio" },
        { source_asset_id: "a2", gender: "male", style_id: "street" }
      ]
    });
    expect(res.tasks).toHaveLength(2);
    const v0 = await getVideo(res.tasks[0].task_id);
    expect(v0.status).toBe("done");
    expect(v0.playback_url).toContain("model-");
  });
});

// ECOM-MODEL-OPTIMIZE-UI-0001 · mock 契约校验（不比 BE 宽松）：合计 ≤6 / 商品图 ≥1 / mode 合法 / 风格互斥 / extra=forbid。
type ModelBody = ModelRequest;
describe("ecom-images model 契约校验（ECOM-MODEL-OPTIMIZE · mock 不比 BE 宽松）", () => {
  it("🔴 商品图 0 张 → 422", async () => {
    await expect(
      estimateEcomModel({ product_asset_ids: [], product_images_mode: "multi_item", gender: "female" })
    ).rejects.toMatchObject({ status: 422 });
  });

  it("🔴 商品图 + 模特图合计 > 6 → 422（商品4 + 模特3 = 7）", async () => {
    await expect(
      estimateEcomModel({ product_asset_ids: ["a1", "a2", "a3", "a4"], model_asset_ids: ["m1", "m2", "m3"], product_images_mode: "multi_item", gender: "female" })
    ).rejects.toMatchObject({ status: 422 });
  });

  it("合计正好 6（商品4 + 模特2）→ 通过", async () => {
    const res = await confirmedModel({ product_asset_ids: ["a1", "a2", "a3", "a4"], model_asset_ids: ["m1", "m2"], product_images_mode: "multi_item", gender: "female" });
    expect(res.task_id).toBeTruthy();
  });

  it("🔴 product_images_mode 非法 → 422", async () => {
    await expect(
      estimateEcomModel({ product_asset_ids: ["a1"], product_images_mode: "bogus", gender: "female" } as unknown as ModelBody)
    ).rejects.toMatchObject({ status: 422 });
  });

  it("🔴 style_id 与 custom_style 同时提供 → 422（互斥）", async () => {
    await expect(
      estimateEcomModel({ product_asset_ids: ["a1"], product_images_mode: "multi_item", gender: "female", style_id: "studio", custom_style: "赛博" })
    ).rejects.toMatchObject({ status: 422 });
  });

  it("🔴 多传字段（extra=forbid）→ 422", async () => {
    await expect(
      estimateEcomModel({ product_asset_ids: ["a1"], product_images_mode: "multi_item", gender: "female", bogus: 1 } as unknown as ModelBody)
    ).rejects.toMatchObject({ status: 422 });
  });

  it("风格可选：不带 style_id / custom_style 也能生成（D3）", async () => {
    const res = await confirmedModel({ product_asset_ids: ["a1"], product_images_mode: "multi_item", gender: "female" });
    expect(res.task_id).toBeTruthy();
  });

  // ── FIX1 真联调对齐 #210 合并源 ──────────────────────────────────
  it("🔴 FIX1 六档风格 id 逐字对齐 #210（含新增 office_commute/resort_travel/high_fashion）", async () => {
    const styles = await listModelStyles();
    expect(styles.map((s) => s.id)).toEqual([
      "studio_white", "lifestyle", "street", "office_commute", "resort_travel", "high_fashion"
    ]);
  });

  it("🔴 FIX1 未知 style_id → 422 ECOM_MODEL_STYLE_INVALID（不比 BE 宽松）", async () => {
    await expect(
      estimateEcomModel({ product_asset_ids: ["a1"], product_images_mode: "multi_item", gender: "female", style_id: "editorial" })
    ).rejects.toMatchObject({ status: 422, code: "ECOM_MODEL_STYLE_INVALID" });
  });

  it("真实新增风格 id（office_commute）可生成", async () => {
    const res = await confirmedModel({ product_asset_ids: ["a1"], product_images_mode: "multi_item", gender: "female", style_id: "office_commute" });
    expect(res.task_id).toBeTruthy();
  });

  it("🔴 FIX1 extra_prompt 超 20000 → 422（BE _ECOM_MODEL_TEXT_LIMIT，非静默截断）", async () => {
    await expect(
      estimateEcomModel({ product_asset_ids: ["a1"], product_images_mode: "multi_item", gender: "female", extra_prompt: "x".repeat(20001) })
    ).rejects.toMatchObject({ status: 422 });
  });

  it("🔴 FIX1 custom_style 超 20000 → 422", async () => {
    await expect(
      estimateEcomModel({ product_asset_ids: ["a1"], product_images_mode: "multi_item", gender: "female", custom_style: "自".repeat(20001) })
    ).rejects.toMatchObject({ status: 422 });
  });

  it("extra_prompt 正好 20000 → 通过（边界）", async () => {
    const res = await confirmedModel({ product_asset_ids: ["a1"], product_images_mode: "multi_item", gender: "female", extra_prompt: "x".repeat(20000) });
    expect(res.task_id).toBeTruthy();
  });
});

describe("ecom-images poster API ↔ MSW（mock 忠实，Phase3 营销海报）", () => {
  it("poster-templates：返回的版式 ID 逐字对齐后端真实预设(promo_bold/minimal/festival)", async () => {
    const templates = await listPosterTemplates();
    expect(templates.length).toBeGreaterThan(0);
    expect(templates[0]).toHaveProperty("id");
    expect(templates[0]).toHaveProperty("name");
    // 承重：mock 模板 id 必须与后端逐字一致，否则真后端 422（改回错 id 此断言应红）。
    const ids = templates.map((t) => t.id);
    expect(ids).toEqual(["promo_bold", "minimal", "festival"]);
  });

  it("单张 poster：塞真 photo task(kind=ecom_poster, done, 海报图 url)，GET /videos/:id 轮询拿到", async () => {
    const res = await posterImage({ source_asset_id: "a1", template_id: "promo_bold", title: "大促", subtitle: "限时" });
    expect(res.task_id).toBeTruthy();
    const v = await getVideo(res.task_id);
    expect(v.status).toBe("done");
    expect((v as { kind?: string }).kind).toBe("ecom_poster");
    expect(v.playback_url).toContain("poster-");
  });

  it("SSE 轮询后海报图 URL 不被通用 v.mp4 覆盖(预 seeded 终态保留)", async () => {
    const res = await posterImage({ source_asset_id: "a1", template_id: "festival", title: "", subtitle: "" });
    await streamVideoEvents(res.task_id, () => undefined);
    const v = await getVideo(res.task_id);
    expect(v.playback_url).toContain("poster-");
    expect(v.playback_url).not.toContain("v.mp4");
  });

  it("批量 poster：第 21 项使整批 422，不静默截断", async () => {
    const items = Array.from({ length: 21 }, (_, i) => ({ source_asset_id: `a${i}`, template_id: "promo_bold", title: "", subtitle: "" }));
    await expect(posterImageBatch({ items })).rejects.toMatchObject({ status: 422 });
  });

  it("批量 poster：fan-out 各 task 可轮询到 done + 海报产物", async () => {
    const res = await posterImageBatch({
      items: [
        { source_asset_id: "a1", template_id: "promo_bold", title: "大促", subtitle: "限时" },
        { source_asset_id: "a2", template_id: "minimal", title: "", subtitle: "" }
      ]
    });
    expect(res.tasks).toHaveLength(2);
    const v0 = await getVideo(res.tasks[0].task_id);
    expect(v0.status).toBe("done");
    expect(v0.playback_url).toContain("poster-");
  });
});
