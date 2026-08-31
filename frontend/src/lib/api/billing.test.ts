import { afterEach, describe, expect, expectTypeOf, it, vi } from "vitest";

import type { BillingOperationLookup, BillingOperationLookupFor } from "./types";

import { ApiError } from "./client";
import {
  billingFromApiError,
  billingHeaders,
  getBillingOperation,
  parseBillingOperationLookup,
  parseBillingQuote,
  parseBillingSummary,
  type BillingOperationLookupLike
} from "./billing";
import type { BillingLookupResultRegistry } from "./billing";

const key = "11111111-1111-4111-8111-111111111111";

afterEach(() => vi.unstubAllGlobals());

function summary(
  status: "reserved" | "settled" | "partially_settled" | "released"
) {
  const amounts = {
    reserved: [30, 30, 0, 0],
    settled: [30, 0, 30, 0],
    partially_settled: [30, 0, 18, 12],
    released: [30, 0, 0, 30]
  }[status];
  return {
    operation_id: "op-1",
    idempotency_key: key,
    status,
    requested_credits: amounts[0],
    held_credits: amounts[1],
    settled_credits: amounts[2],
    released_credits: amounts[3]
  };
}

describe("billing wire parsing", () => {
  it.each(["reserved", "settled", "partially_settled", "released"] as const)(
    "accepts a conserved %s summary",
    (status) => expect(parseBillingSummary(summary(status))?.status).toBe(status)
  );

  it("accepts a legal zero-price settled operation", () => {
    expect(
      parseBillingSummary({
        ...summary("settled"),
        requested_credits: 0,
        settled_credits: 0
      })?.status
    ).toBe("settled");
  });

  it("rejects a terminal summary whose money does not conserve", () => {
    expect(
      parseBillingSummary({ ...summary("settled"), settled_credits: 29 })
    ).toBeNull();
  });

  it.each([
    { ...summary("reserved"), held_credits: -1 },
    { ...summary("settled"), settled_credits: 30.5 },
    { ...summary("released"), status: "refunded" },
    { ...summary("released"), idempotency_key: "not-a-uuid" },
    { ...summary("released"), extra: true }
  ])("rejects malformed or unknown summary data", (value) => {
    expect(parseBillingSummary(value)).toBeNull();
  });

  it("reads billing from ApiError.detail but never from an envelope root", () => {
    const error = new ApiError("失败", "supplier_failed", 502, {
      billing: summary("released")
    });
    expect(billingFromApiError(error)?.status).toBe("released");
    expect(billingFromApiError({ billing: summary("released") })).toBeNull();
  });

  it("uses only the two authoritative confirmation header names", () => {
    expect(billingHeaders({ quote_token: "quote-token", idempotency_key: key })).toEqual({
      "Idempotency-Key": key,
      "X-Huading-Quote": "quote-token"
    });
  });
});

function disclosure(overrides: Record<string, unknown> = {}) {
  return {
    key: "pricing.voice.characters",
    rendered_text: "按字符计费",
    copy_version: 1,
    unit: "character",
    rate_scope: "tenant_overridable",
    rate_source: "tenant_rate",
    rate_id: "rate-1",
    effective_at: "2026-08-29T10:00:00Z",
    policy_key: null,
    policy_version: null,
    reference_unit_credits: "0.1",
    ...overrides
  };
}

function line(overrides: Record<string, unknown> = {}) {
  return {
    operation: "brand_voice_clone",
    capability: "voice.clone",
    unit: "character",
    quantity: "100",
    unit_credits: "0.1",
    subtotal_credits: "10",
    rate_scope: "tenant_overridable",
    rate_source: "tenant_rate",
    rate_id: "rate-1",
    effective_at: "2026-08-29T10:00:00Z",
    policy_key: null,
    policy_version: null,
    label: "声音克隆",
    ...overrides
  };
}

function simpleQuote(overrides: Record<string, unknown> = {}) {
  return {
    pricing_contract: "billing_quote",
    operation: "brand_voice_clone",
    pricing_shape: "simple",
    unit: "character",
    quantity: "100",
    unit_credits: "0.1",
    rate_scope: "tenant_overridable",
    rate_source: "tenant_rate",
    subtotal_credits: "10",
    payable_credits: 10,
    breakdown: [],
    disclosures: [disclosure()],
    quote_token: "signed-quote",
    expires_at: "2026-08-29T10:05:00Z",
    ...overrides
  };
}

describe("billing quote parsing", () => {
  it("accepts a simple quote without recalculating server money", () => {
    const parsed = parseBillingQuote(simpleQuote({ unit_credits: "0.333", subtotal_credits: "10.01" }));
    expect(parsed?.subtotal_credits).toBe("10.01");
    expect(parsed?.payable_credits).toBe(10);
  });

  it("accepts a composite quote only with null top-level unit fields and a non-empty breakdown", () => {
    const parsed = parseBillingQuote(
      simpleQuote({
        pricing_shape: "composite",
        unit: null,
        quantity: null,
        unit_credits: null,
        rate_scope: null,
        rate_source: null,
        breakdown: [line()]
      })
    );
    expect(parsed?.pricing_shape).toBe("composite");
  });

  const videoLine = () =>
    line({ operation: "video_create", capability: "video", unit: "second" });
  const cosyvoiceLine = () =>
    line({ operation: "cosyvoice_brand_tts", capability: "tts", unit: "character" });
  const videoComposite = (breakdown: Record<string, unknown>[]) =>
    simpleQuote({
      operation: "video_create",
      pricing_shape: "composite",
      unit: null,
      quantity: null,
      unit_credits: null,
      rate_scope: null,
      rate_source: null,
      breakdown
    });

  it("requires exactly one video_create line", () => {
    const doubao = { voice_kind: "brand", voice_provider: "doubao" } as const;
    expect(
      parseBillingQuote(
        videoComposite([cosyvoiceLine()]),
        { voice_kind: "brand", voice_provider: "cosyvoice" }
      )
    ).toBeNull();
    expect(parseBillingQuote(videoComposite([videoLine(), videoLine()]), doubao)).toBeNull();
  });

  it("requires exactly one CosyVoice TTS line for the selected CosyVoice provider", () => {
    const cosyvoice = { voice_kind: "brand", voice_provider: "cosyvoice" } as const;
    expect(parseBillingQuote(videoComposite([videoLine()]), cosyvoice)).toBeNull();
    expect(
      parseBillingQuote(
        videoComposite([videoLine(), cosyvoiceLine(), cosyvoiceLine()]),
        cosyvoice
      )
    ).toBeNull();
    expect(parseBillingQuote(videoComposite([videoLine(), cosyvoiceLine()]), cosyvoice))
      .not.toBeNull();
  });

  it("rejects a fake CosyVoice TTS line for the selected Doubao provider", () => {
    const doubao = { voice_kind: "brand", voice_provider: "doubao" } as const;
    expect(parseBillingQuote(videoComposite([videoLine(), cosyvoiceLine()]), doubao)).toBeNull();
    expect(parseBillingQuote(videoComposite([videoLine()]), doubao)).not.toBeNull();
  });

  it("fails closed for video quotes without selected-provider context", () => {
    expect(parseBillingQuote(videoComposite([videoLine()]))).toBeNull();
    expect(
      parseBillingQuote(
        videoComposite([videoLine()]),
        { voice_kind: "brand", voice_provider: "unknown" } as never
      )
    ).toBeNull();
  });

  it.each([
    simpleQuote({ quantity: "NaN" }),
    simpleQuote({ unit_credits: "1e3" }),
    simpleQuote({ subtotal_credits: "-1" }),
    simpleQuote({ payable_credits: 10.5 }),
    simpleQuote({ expires_at: "2026-02-30T10:00:00Z" }),
    simpleQuote({ breakdown: [line()] }),
    simpleQuote({ pricing_shape: "composite", breakdown: [] }),
    simpleQuote({ rate_source: "unknown_rate" }),
    simpleQuote({ pricing_contract: "legacy_estimate" }),
    simpleQuote({ extra: "forbidden" })
  ])("rejects malformed money, shape, contract, and unknown fields", (value) => {
    expect(parseBillingQuote(value)).toBeNull();
  });

  it.each([
    disclosure({ rate_id: null }),
    disclosure({ effective_at: null }),
    disclosure({ policy_key: "wrong" }),
    disclosure({ rate_source: "fixed_policy", rate_id: "wrong", effective_at: null }),
    disclosure({
      rate_source: "fixed_policy",
      rate_id: null,
      effective_at: null,
      policy_key: null,
      policy_version: 1
    }),
    disclosure({
      rate_source: "fixed_policy",
      rate_id: null,
      effective_at: null,
      policy_key: "cosyvoice_free",
      policy_version: 0
    })
  ])("rejects incomplete disclosure provenance", (value) => {
    expect(parseBillingQuote(simpleQuote({ disclosures: [value] }))).toBeNull();
  });

  it("accepts complete fixed-policy provenance on lines and disclosures", () => {
    const provenance = {
      rate_source: "fixed_policy",
      rate_scope: "platform_fixed",
      rate_id: null,
      effective_at: null,
      policy_key: "cosyvoice_free",
      policy_version: 1
    };
    expect(
      parseBillingQuote(
        simpleQuote({
          pricing_shape: "composite",
          unit: null,
          quantity: null,
          unit_credits: null,
          rate_scope: null,
          rate_source: null,
          breakdown: [line(provenance)],
          disclosures: [disclosure(provenance)],
          payable_credits: 0,
          subtotal_credits: "0"
        })
      )
    ).not.toBeNull();
  });

  it("rejects malformed decimal and incomplete provenance inside a composite line", () => {
    const composite = (entry: Record<string, unknown>) =>
      simpleQuote({
        pricing_shape: "composite",
        unit: null,
        quantity: null,
        unit_credits: null,
        rate_scope: null,
        rate_source: null,
        breakdown: [entry]
      });
    expect(parseBillingQuote(composite(line({ quantity: "Infinity" })))).toBeNull();
    expect(parseBillingQuote(composite(line({ rate_id: null })))).toBeNull();
  });
});

function lookupBase(overrides: Record<string, unknown> = {}) {
  return {
    operation: "cosyvoice_brand_voice_create",
    idempotency_key: key,
    state: "in_progress",
    completion_kind: null,
    billing: summary("reserved"),
    result_type: null,
    result_id: null,
    resource: null,
    result: null,
    failure: null,
    ...overrides
  };
}

function brandVoicePayload(overrides: Record<string, unknown> = {}) {
  return {
    id: "voice-1",
    name: "品牌音色",
    provider: "cosyvoice-voice-clone",
    status: "ready",
    order_status: null,
    delivery_status: "active",
    expires_at: null,
    created_at: "2026-08-29T10:00:00Z",
    ...overrides
  };
}

function brandVoiceOrderPayload(
  status: "awaiting_fulfillment" | "fulfilled" | "rejected",
  overrides: Record<string, unknown> = {}
) {
  return {
    id: "order-1",
    tenant_id: "tenant-1",
    ordered_by_user_id: "user-1",
    order_type: "create",
    requested_name: "品牌音色",
    source_audio_asset_id: "asset-1",
    existing_brand_voice_id: null,
    status,
    fulfilled_brand_voice_id: status === "fulfilled" ? "voice-1" : null,
    fulfilled_provider_voice_id: status === "fulfilled" ? "provider-voice-1" : null,
    rejection_reason: status === "rejected" ? "音频不合格" : null,
    fulfilled_at: status === "fulfilled" ? "2026-08-29T10:02:00Z" : null,
    expires_at: status === "fulfilled" ? "2027-08-29T10:02:00Z" : null,
    rejected_at: status === "rejected" ? "2026-08-29T10:02:00Z" : null,
    created_at: "2026-08-29T10:00:00Z",
    updated_at: "2026-08-29T10:02:00Z",
    billing: summary(status === "awaiting_fulfillment" ? "reserved" : status === "fulfilled" ? "settled" : "released"),
    refund_disposition: status === "rejected" ? "source_subscription_released" : "not_applicable",
    refund_grant_status: null,
    refund_applied_at: null,
    ...overrides
  };
}

describe("billing operation lookup parsing", () => {
  it("types canonical lookup payloads by their real state", () => {
    type VideoSucceeded = Extract<
      BillingOperationLookup,
      { completion_kind: "succeeded"; result_type: "video_task" }
    >;
    type OrderSucceeded = Extract<
      BillingOperationLookup,
      { completion_kind: "succeeded"; result_type: "brand_voice_order" }
    >;
    type OrderInProgress = Extract<
      BillingOperationLookup,
      { state: "in_progress"; result_type: "brand_voice_order" }
    >;
    type VoiceSucceeded = Extract<
      BillingOperationLookup,
      { completion_kind: "succeeded"; result_type: "brand_voice" }
    >;

    expectTypeOf<VideoSucceeded["result"]["status"]>().toEqualTypeOf<"done">();
    expectTypeOf<OrderSucceeded["result"]["status"]>().toEqualTypeOf<"fulfilled">();
    expectTypeOf<NonNullable<OrderInProgress["resource"]>["status"]>()
      .toEqualTypeOf<"awaiting_fulfillment">();
    expectTypeOf<VoiceSucceeded["result"]["status"]>().toEqualTypeOf<"ready">();
    expectTypeOf<VoiceSucceeded["result"]["provider"]>()
      .toEqualTypeOf<"cosyvoice-voice-clone">();
  });

  it("rejects an unregistered successful result type by default", () => {
    expect(
      parseBillingOperationLookup(
        lookupBase({
          state: "completed",
          completion_kind: "succeeded",
          billing: summary("settled"),
          result_type: "future_unregistered_result",
          result_id: "resource-1",
          result: { id: "resource-1" },
          resource: { id: "resource-1" }
        })
      )
    ).toBeNull();
  });

  it("rejects a registered result type whose payload does not match its schema", () => {
    expect(
      parseBillingOperationLookup(
        lookupBase({
          operation: "cosyvoice_brand_voice_create",
          state: "completed",
          completion_kind: "succeeded",
          billing: summary("settled"),
          result_type: "brand_voice",
          result_id: "voice-1",
          result: { id: "voice-1" },
          resource: { id: "voice-1" }
        })
      )
    ).toBeNull();
  });

  it("accepts all four closed lookup variants", () => {
    const succeeded = lookupBase({
      state: "completed",
      completion_kind: "succeeded",
      billing: summary("settled"),
      result_type: "brand_voice",
      result_id: "voice-1",
      result: brandVoicePayload(),
      resource: brandVoicePayload()
    });
    const rejected = lookupBase({
      operation: "doubao_brand_voice_order_create",
      state: "completed",
      completion_kind: "rejected",
      billing: summary("released"),
      result_type: "brand_voice_order",
      result_id: "order-1",
      resource: brandVoiceOrderPayload("rejected")
    });
    const failed = lookupBase({
      state: "completed",
      completion_kind: "failed",
      billing: summary("released"),
      failure: {
        code: "PROVIDER_FAILED",
        original_http_status: 502,
        detail: { requires_new_quote: true }
      }
    });
    expect(parseBillingOperationLookup(lookupBase())?.state).toBe("in_progress");
    expect(parseBillingOperationLookup(succeeded)?.completion_kind).toBe("succeeded");
    expect(parseBillingOperationLookup(rejected)?.completion_kind).toBe("rejected");
    expect(parseBillingOperationLookup(failed)?.completion_kind).toBe("failed");
  });

  it("accepts a reserved CosyVoice operation with a preallocated result ID", () => {
    const parsed = parseBillingOperationLookup(
      lookupBase({
        operation: "cosyvoice_brand_voice_create",
        result_type: null,
        result_id: "voice-preallocated-1",
        resource: null
      })
    );

    expect(parsed).toMatchObject({
      state: "in_progress",
      result_type: null,
      result_id: "voice-preallocated-1",
      resource: null
    });
  });

  it.each([
    ["script_generate", "script_generate_result", null, { script: "成稿" }],
    [
      "scene_prompt",
      "scene_prompt_result",
      null,
      { scene_prompt: "产品特写", negative_prompt: "模糊" }
    ],
    [
      "ecom_cutout",
      "ecom_image_batch",
      "33333333-3333-4333-8333-333333333333",
      {
        items: [
          {
            item_index: 0,
            task_id: "task-1",
            source_asset_id: "asset-1",
            status: "done",
            asset_id: "output-1"
          }
        ]
      }
    ],
    ["video_create", "video_task", "task-1", { task_id: "task-1", status: "done" }],
    [
      "doubao_brand_voice_order_create",
      "brand_voice_order",
      "order-1",
      brandVoiceOrderPayload("fulfilled")
    ],
    [
      "doubao_brand_voice_order_renew",
      "brand_voice_order",
      "order-renew-1",
      brandVoiceOrderPayload("fulfilled", {
        id: "order-renew-1",
        order_type: "renew",
        existing_brand_voice_id: "voice-existing-1"
      })
    ],
    ["cosyvoice_brand_voice_create", "brand_voice", "voice-1", brandVoicePayload()]
  ])("accepts the registered %s / %s result schema", (operation, resultType, resultId, payload) => {
    const parsed = parseBillingOperationLookup(
      lookupBase({
        operation,
        state: "completed",
        completion_kind: "succeeded",
        billing: summary("settled"),
        result_type: resultType,
        result_id: resultId,
        result: payload,
        resource: resultId === null ? null : payload
      })
    );
    expect(parsed?.completion_kind).toBe("succeeded");
  });

  it.each([
    ["script_generate", "script_generate_result", null, { script: 42 }],
    ["scene_prompt", "scene_prompt_result", null, { scene_prompt: "产品特写" }],
    [
      "ecom_cutout",
      "ecom_image_batch",
      "33333333-3333-4333-8333-333333333333",
      {
        items: [
          {
            item_index: 0,
            task_id: "task-1",
            source_asset_id: "asset-1",
            status: "done"
          }
        ]
      }
    ],
    ["video_create", "video_task", "task-1", { task_id: "task-1", status: "success" }],
    [
      "doubao_brand_voice_order_create",
      "brand_voice_order",
      "order-1",
      brandVoiceOrderPayload("fulfilled", { unexpected: true })
    ],
    [
      "cosyvoice_brand_voice_create",
      "brand_voice",
      "voice-1",
      brandVoicePayload({ created_at: "not-a-date" })
    ]
  ])("rejects malformed canonical %s / %s payloads", (operation, resultType, resultId, payload) => {
    expect(
      parseBillingOperationLookup(
        lookupBase({
          operation,
          state: "completed",
          completion_kind: "succeeded",
          billing: summary("settled"),
          result_type: resultType,
          result_id: resultId,
          result: payload,
          resource: resultId === null ? null : payload
        })
      )
    ).toBeNull();
  });

  it("rejects an awaiting manual brand voice order as a succeeded lookup", () => {
    const payload = brandVoiceOrderPayload("awaiting_fulfillment", {
      billing: summary("settled")
    });
    expect(
      parseBillingOperationLookup(
        lookupBase({
          operation: "doubao_brand_voice_order_create",
          state: "completed",
          completion_kind: "succeeded",
          billing: summary("settled"),
          result_type: "brand_voice_order",
          result_id: "order-1",
          result: payload,
          resource: payload
        })
      )
    ).toBeNull();
  });

  it("accepts an awaiting manual brand voice order as reserved in progress", () => {
    const resource = brandVoiceOrderPayload("awaiting_fulfillment");
    expect(
      parseBillingOperationLookup(
        lookupBase({
          operation: "doubao_brand_voice_order_create",
          result_type: "brand_voice_order",
          result_id: "order-1",
          resource
        })
      )
    ).toMatchObject({
      state: "in_progress",
      completion_kind: null,
      result_type: "brand_voice_order",
      result_id: "order-1",
      resource
    });
  });

  it("rejects a manual order whose nested billing differs from the lookup summary", () => {
    const payload = brandVoiceOrderPayload("fulfilled", {
      billing: {
        ...summary("settled"),
        requested_credits: 20,
        settled_credits: 20
      }
    });
    expect(
      parseBillingOperationLookup(
        lookupBase({
          operation: "doubao_brand_voice_order_create",
          state: "completed",
          completion_kind: "succeeded",
          billing: summary("settled"),
          result_type: "brand_voice_order",
          result_id: "order-1",
          result: payload,
          resource: payload
        })
      )
    ).toBeNull();
  });

  it("rejects a queued video task as a completed succeeded lookup", () => {
    const payload = { task_id: "task-1", status: "queued" };
    expect(
      parseBillingOperationLookup(
        lookupBase({
          operation: "video_create",
          state: "completed",
          completion_kind: "succeeded",
          billing: summary("settled"),
          result_type: "video_task",
          result_id: "task-1",
          result: payload,
          resource: payload
        })
      )
    ).toBeNull();
  });

  it("rejects a completed ecom item without its required output asset", () => {
    const payload = {
      items: [
        {
          item_index: 0,
          task_id: "task-1",
          source_asset_id: "asset-1",
          status: "done",
          asset_id: null
        }
      ]
    };
    expect(
      parseBillingOperationLookup(
        lookupBase({
          operation: "ecom_cutout",
          state: "completed",
          completion_kind: "succeeded",
          billing: summary("settled"),
          result_type: "ecom_image_batch",
          result_id: "33333333-3333-4333-8333-333333333333",
          result: payload,
          resource: payload
        })
      )
    ).toBeNull();
  });

  it("rejects a processing brand voice as a completed succeeded lookup", () => {
    const payload = brandVoicePayload({
      status: "processing",
      delivery_status: "awaiting_fulfillment"
    });
    expect(
      parseBillingOperationLookup(
        lookupBase({
          operation: "cosyvoice_brand_voice_create",
          state: "completed",
          completion_kind: "succeeded",
          billing: summary("settled"),
          result_type: "brand_voice",
          result_id: "voice-1",
          result: payload,
          resource: payload
        })
      )
    ).toBeNull();
  });

  it("rejects an impossible refund tuple on a rejected manual order", () => {
    const payload = brandVoiceOrderPayload("rejected", {
      refund_disposition: "not_applicable",
      refund_grant_status: null,
      refund_applied_at: null
    });
    expect(
      parseBillingOperationLookup(
        lookupBase({
          operation: "doubao_brand_voice_order_create",
          state: "completed",
          completion_kind: "rejected",
          billing: summary("released"),
          result_type: "brand_voice_order",
          result_id: "order-1",
          resource: payload
        })
      )
    ).toBeNull();
  });

  it("rejects known payloads with the wrong operation, ID, resource copy, or error extension", () => {
    const valid = {
      operation: "cosyvoice_brand_voice_create",
      state: "completed",
      completion_kind: "succeeded",
      billing: summary("settled"),
      result_type: "brand_voice",
      result_id: "voice-1",
      result: brandVoicePayload(),
      resource: brandVoicePayload()
    };
    expect(parseBillingOperationLookup(lookupBase({ ...valid, operation: "video_create" }))).toBeNull();
    expect(parseBillingOperationLookup(lookupBase({ ...valid, result_id: "voice-2" }))).toBeNull();
    expect(
      parseBillingOperationLookup(
        lookupBase({ ...valid, resource: brandVoicePayload({ name: "另一音色" }) })
      )
    ).toBeNull();
    expect(
      parseBillingOperationLookup(
        lookupBase({
          operation: "video_create",
          state: "completed",
          completion_kind: "failed",
          billing: summary("released"),
          failure: {
            code: "PROVIDER_FAILED",
            original_http_status: 502,
            detail: { requires_new_quote: true, supplier_trace: "forbidden" }
          }
        })
      )
    ).toBeNull();
  });

  it.each([
    lookupBase({ state: "pending" }),
    lookupBase({ completion_kind: "succeeded" }),
    lookupBase({
      idempotency_key: "22222222-2222-4222-8222-222222222222",
      billing: summary("reserved")
    }),
    lookupBase({ extra: true }),
    lookupBase({
      state: "completed",
      completion_kind: "succeeded",
      billing: summary("settled"),
      result_type: "voice",
      result: null
    }),
    lookupBase({
      state: "completed",
      completion_kind: "rejected",
      billing: summary("released"),
      result_type: "order",
      resource: { id: "order-1" },
      result: { forbidden: true }
    }),
    lookupBase({
      state: "completed",
      completion_kind: "failed",
      billing: summary("released"),
      result_type: "forbidden",
      failure: { code: "provider failed", original_http_status: 502, detail: null }
    })
  ])("rejects unknown states and non-exclusive or inconsistent payloads", (value) => {
    expect(parseBillingOperationLookup(value)).toBeNull();
  });

  it("queries the encoded by-idempotency route and validates the returned lookup", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          data: lookupBase({ operation: "script_generate" }),
          error: null,
          request_id: "req-1"
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(getBillingOperation("script_generate", key)).resolves.toMatchObject({
      state: "in_progress",
      idempotency_key: key
    });
    expect(fetchMock.mock.calls[0][0]).toContain(
      `/api/v1/billing/operations/by-idempotency/script_generate/${key}`
    );
  });

  it("uses an explicit strict parser for an extended lookup operation", async () => {
    const registry: BillingLookupResultRegistry = {
      custom_result: {
        operations: ["custom_operation"],
        completionKinds: ["succeeded"],
        parse: (value) =>
          typeof value === "object" &&
          value !== null &&
          !Array.isArray(value) &&
          Object.keys(value).length === 1 &&
          typeof (value as { id?: unknown }).id === "string"
            ? (value as Record<string, unknown>)
            : null,
        validateResultId: (resultId, payload) => payload?.id === resultId
      }
    };
    const custom = lookupBase({
      operation: "custom_operation",
      state: "completed",
      completion_kind: "succeeded",
      billing: summary("settled"),
      result_type: "custom_result",
      result_id: "custom-1",
      resource: { id: "custom-1" },
      result: { id: "custom-1" }
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ data: custom, error: null, request_id: "req-1" }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
    );
    type CustomTransportLookup = BillingOperationLookupLike & {
      operation: "custom_operation";
      state: "completed";
      completion_kind: "succeeded";
      result_type: "custom_result";
      result: { id: string };
    };
    const parseCustom = (value: unknown): CustomTransportLookup | null => {
      const parsed = parseBillingOperationLookup(value, registry);
      return parsed?.operation === "custom_operation" && parsed.result_type === "custom_result"
        ? (parsed as CustomTransportLookup)
        : null;
    };

    await expect(
      getBillingOperation("custom_operation", key, parseCustom)
    ).resolves.toMatchObject({ result_type: "custom_result", result_id: "custom-1" });
  });

  it("converts a throwing explicit lookup parser into a protocol rejection", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ data: lookupBase(), error: null, request_id: "req-1" }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
    );
    const throwingParser = new Proxy(
      (): BillingOperationLookupFor<"script_generate"> | null => null,
      {
        apply() {
          throw new Error("lookup parser apply trap");
        }
      }
    );

    await expect(
      getBillingOperation("script_generate", key, throwingParser)
    ).rejects.toMatchObject({ code: "INVALID_BILLING_RESPONSE" });
  });

  it("fails closed when a successful lookup response has an invalid wire shape", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ data: { state: "completed" }, error: null, request_id: "req-1" }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
    );
    await expect(getBillingOperation("script_generate", key)).rejects.toMatchObject({
      code: "INVALID_BILLING_RESPONSE"
    });
  });

  it("preserves only a valid data-envelope billing summary when the typed payload is malformed", async () => {
    const malformed = lookupBase({
      operation: "cosyvoice_brand_voice_create",
      state: "completed",
      completion_kind: "succeeded",
      billing: summary("settled"),
      result_type: "brand_voice",
      result_id: "voice-1",
      resource: brandVoicePayload({ status: "processing" }),
      result: brandVoicePayload({ status: "processing" })
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            data: malformed,
            billing: summary("released"),
            error: null,
            request_id: "req-1"
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
    );

    await expect(
      getBillingOperation("cosyvoice_brand_voice_create", key)
    ).rejects.toMatchObject({
      code: "INVALID_BILLING_RESPONSE",
      detail: { billing: summary("settled") }
    });
  });

  it("marks only explicitly registered lookup extensions as extended", () => {
    const registry: BillingLookupResultRegistry = {
      custom_result: {
        operations: ["custom_operation"],
        completionKinds: ["succeeded"],
        parse: (value) =>
          typeof value === "object" &&
          value !== null &&
          !Array.isArray(value) &&
          Object.keys(value).length === 1 &&
          typeof (value as { id?: unknown }).id === "string"
            ? (value as Record<string, unknown>)
            : null,
        validateResultId: (resultId, payload) => payload?.id === resultId
      }
    };
    const custom = lookupBase({
      operation: "custom_operation",
      state: "completed",
      completion_kind: "succeeded",
      billing: summary("settled"),
      result_type: "custom_result",
      result_id: "custom-1",
      resource: { id: "custom-1" },
      result: { id: "custom-1" }
    });

    const builtIn = parseBillingOperationLookup(lookupBase());
    const extended = parseBillingOperationLookup(custom, registry);
    expectTypeOf(builtIn).toEqualTypeOf<BillingOperationLookup | null>();
    expectTypeOf(extended).toEqualTypeOf<BillingOperationLookupLike | null>();
    expect(extended?.result_type).toBe("custom_result");
  });

  it("does not let an extension registry override a canonical result schema", () => {
    const override: BillingLookupResultRegistry = {
      brand_voice: {
        operations: ["cosyvoice_brand_voice_create"],
        completionKinds: ["succeeded"],
        parse: (value) =>
          typeof value === "object" && value !== null
            ? (value as Record<string, unknown>)
            : null,
        validateResultId: () => true
      }
    };
    expect(
      parseBillingOperationLookup(
        lookupBase({
          operation: "cosyvoice_brand_voice_create",
          state: "completed",
          completion_kind: "succeeded",
          billing: summary("settled"),
          result_type: "brand_voice",
          result_id: "voice-1",
          resource: { id: "voice-1" },
          result: { id: "voice-1" }
        }),
        override
      )
    ).toBeNull();
    expect(parseBillingOperationLookup(lookupBase())).not.toBeNull();
  });

  it("does not let a new extension result type attach to a canonical operation", () => {
    const unrelated: BillingLookupResultRegistry = {
      custom_result: {
        operations: ["script_generate"],
        completionKinds: ["succeeded"],
        parse: (value) =>
          typeof value === "object" &&
          value !== null &&
          !Array.isArray(value) &&
          Object.keys(value).length === 1 &&
          typeof (value as { id?: unknown }).id === "string"
            ? (value as Record<string, unknown>)
            : null,
        validateResultId: (resultId, payload) => payload?.id === resultId
      }
    };
    expect(
      parseBillingOperationLookup(
        lookupBase({
          operation: "script_generate",
          state: "completed",
          completion_kind: "succeeded",
          billing: summary("settled"),
          result_type: "custom_result",
          result_id: "custom-1",
          resource: { id: "custom-1" },
          result: { id: "custom-1" }
        }),
        unrelated
      )
    ).toBeNull();
  });

  it.each(["toString", "constructor", "__proto__", "prototype"])(
    "rejects the prototype result type %s without throwing",
    (resultType) => {
      const candidate = lookupBase({
        operation: "script_generate",
        state: "completed",
        completion_kind: "succeeded",
        billing: summary("settled"),
        result_type: resultType,
        result_id: null,
        resource: null,
        result: { script: "成稿" }
      });

      expect(() => parseBillingOperationLookup(candidate)).not.toThrow();
      expect(parseBillingOperationLookup(candidate)).toBeNull();
    }
  );

  it.each(["toString", "constructor", "__proto__", "prototype"])(
    "rejects the own prototype-key extension %s in a null-prototype registry",
    (prototypeKey) => {
      const registry = Object.create(null) as Record<string, unknown>;
      Object.defineProperty(registry, prototypeKey, {
        enumerable: true,
        value: {
          operations: ["custom_operation"],
          completionKinds: ["succeeded"],
          parse: (value: unknown) =>
            typeof value === "object" && value !== null
              ? (value as Record<string, unknown>)
              : null,
          validateResultId: () => true
        }
      });
      const candidate = lookupBase({
        operation: "custom_operation",
        state: "completed",
        completion_kind: "succeeded",
        billing: summary("settled"),
        result_type: prototypeKey,
        result_id: "result-1",
        resource: { id: "result-1" },
        result: { id: "result-1" }
      });

      expect(() =>
        parseBillingOperationLookup(candidate, registry as BillingLookupResultRegistry)
      ).not.toThrow();
      expect(
        parseBillingOperationLookup(candidate, registry as BillingLookupResultRegistry)
      ).toBeNull();
    }
  );

  it("ignores inherited extension entries", () => {
    const inheritedSchema = {
      operations: ["inherited_operation"],
      completionKinds: ["succeeded"] as const,
      parse: (value: unknown) =>
        typeof value === "object" && value !== null
          ? (value as Record<string, unknown>)
          : null,
      validateResultId: () => true
    };
    const registry = Object.create({ inherited_result: inheritedSchema }) as BillingLookupResultRegistry;
    const candidate = lookupBase({
      operation: "inherited_operation",
      state: "completed",
      completion_kind: "succeeded",
      billing: summary("settled"),
      result_type: "inherited_result",
      result_id: "result-1",
      resource: { id: "result-1" },
      result: { id: "result-1" }
    });

    expect(() => parseBillingOperationLookup(candidate, registry)).not.toThrow();
    expect(parseBillingOperationLookup(candidate, registry)).toBeNull();
  });

  it("rejects extension registry getters without invoking them", () => {
    let getterCalled = false;
    const registry = Object.defineProperty({}, "custom_result", {
      enumerable: true,
      get() {
        getterCalled = true;
        throw new Error("registry getter must not run");
      }
    }) as BillingLookupResultRegistry;

    expect(() => parseBillingOperationLookup(lookupBase(), registry)).not.toThrow();
    expect(parseBillingOperationLookup(lookupBase(), registry)).toBeNull();
    expect(getterCalled).toBe(false);
  });

  it("rejects extension schema getters without invoking them", () => {
    let getterCalled = false;
    const schema = Object.defineProperty(
      {
        completionKinds: ["succeeded"],
        parse: () => null,
        validateResultId: () => false
      },
      "operations",
      {
        enumerable: true,
        get() {
          getterCalled = true;
          throw new Error("schema getter must not run");
        }
      }
    );
    const registry = { custom_result: schema } as unknown as BillingLookupResultRegistry;

    expect(() => parseBillingOperationLookup(lookupBase(), registry)).not.toThrow();
    expect(parseBillingOperationLookup(lookupBase(), registry)).toBeNull();
    expect(getterCalled).toBe(false);
  });

  it("rejects an extension operations includes getter without invoking it", () => {
    let getterCalls = 0;
    const operations = ["custom_operation"];
    Object.defineProperty(operations, "includes", {
      get() {
        getterCalls += 1;
        throw new Error("operations.includes getter must not run");
      }
    });
    const registry = {
      custom_result: {
        operations,
        completionKinds: ["succeeded"],
        parse: (value: unknown) =>
          typeof value === "object" && value !== null
            ? (value as Record<string, unknown>)
            : null,
        validateResultId: () => true
      }
    } as BillingLookupResultRegistry;
    const candidate = lookupBase({
      operation: "custom_operation",
      state: "completed",
      completion_kind: "succeeded",
      billing: summary("settled"),
      result_type: "custom_result",
      result_id: "result-1",
      resource: { id: "result-1" },
      result: { id: "result-1" }
    });

    expect(() => parseBillingOperationLookup(candidate, registry)).not.toThrow();
    expect(parseBillingOperationLookup(candidate, registry)).toBeNull();
    expect(getterCalls).toBe(0);
  });

  it("rejects an extension array with a polluted prototype without walking it", () => {
    let getterCalls = 0;
    const operations = ["custom_operation"];
    const pollutedPrototype = Object.create(Array.prototype) as unknown[];
    Object.defineProperty(pollutedPrototype, "includes", {
      get() {
        getterCalls += 1;
        throw new Error("polluted array prototype must not be read");
      }
    });
    Object.setPrototypeOf(operations, pollutedPrototype);
    const registry = {
      custom_result: {
        operations,
        completionKinds: ["succeeded"],
        parse: (value: unknown) =>
          typeof value === "object" && value !== null
            ? (value as Record<string, unknown>)
            : null,
        validateResultId: () => true
      }
    } as BillingLookupResultRegistry;
    const candidate = lookupBase({
      operation: "custom_operation",
      state: "completed",
      completion_kind: "succeeded",
      billing: summary("settled"),
      result_type: "custom_result",
      result_id: "result-1",
      resource: { id: "result-1" },
      result: { id: "result-1" }
    });

    expect(parseBillingOperationLookup(candidate, registry)).toBeNull();
    expect(getterCalls).toBe(0);
  });

  it("rejects a completionKinds index getter without invoking it", () => {
    let getterCalls = 0;
    const completionKinds = new Array<string>(1);
    Object.defineProperty(completionKinds, "0", {
      enumerable: true,
      configurable: true,
      get() {
        getterCalls += 1;
        throw new Error("completionKinds index getter must not run");
      }
    });
    const registry = {
      custom_result: {
        operations: ["custom_operation"],
        completionKinds,
        parse: () => null,
        validateResultId: () => false
      }
    } as unknown as BillingLookupResultRegistry;

    expect(() => parseBillingOperationLookup(lookupBase(), registry)).not.toThrow();
    expect(parseBillingOperationLookup(lookupBase(), registry)).toBeNull();
    expect(getterCalls).toBe(0);
  });

  it("rejects a sparse completionKinds array", () => {
    const registry = {
      custom_result: {
        operations: ["custom_operation"],
        completionKinds: new Array<string>(1),
        parse: () => null,
        validateResultId: () => false
      }
    } as unknown as BillingLookupResultRegistry;

    expect(() => parseBillingOperationLookup(lookupBase(), registry)).not.toThrow();
    expect(parseBillingOperationLookup(lookupBase(), registry)).toBeNull();
  });

  it("fails closed when an extension schema Proxy ownKeys trap throws", () => {
    const schema = new Proxy(
      {},
      {
        ownKeys() {
          throw new Error("schema ownKeys trap");
        }
      }
    );
    const registry = { custom_result: schema } as unknown as BillingLookupResultRegistry;

    expect(() => parseBillingOperationLookup(lookupBase(), registry)).not.toThrow();
    expect(parseBillingOperationLookup(lookupBase(), registry)).toBeNull();
  });

  it("fails closed when an extension registry Proxy descriptor trap throws", () => {
    const registry = new Proxy(
      { custom_result: {} },
      {
        getOwnPropertyDescriptor() {
          throw new Error("registry descriptor trap");
        }
      }
    ) as unknown as BillingLookupResultRegistry;

    expect(() => parseBillingOperationLookup(lookupBase(), registry)).not.toThrow();
    expect(parseBillingOperationLookup(lookupBase(), registry)).toBeNull();
  });

  it("fails closed when an extension parser Proxy apply trap throws", () => {
    const parse = new Proxy(
      () => null,
      {
        apply() {
          throw new Error("parser apply trap");
        }
      }
    );
    const registry = {
      custom_result: {
        operations: ["custom_operation"],
        completionKinds: ["succeeded"],
        parse,
        validateResultId: () => true
      }
    } as BillingLookupResultRegistry;
    const candidate = lookupBase({
      operation: "custom_operation",
      state: "completed",
      completion_kind: "succeeded",
      billing: summary("settled"),
      result_type: "custom_result",
      result_id: "result-1",
      resource: { id: "result-1" },
      result: { id: "result-1" }
    });

    expect(() => parseBillingOperationLookup(candidate, registry)).not.toThrow();
    expect(parseBillingOperationLookup(candidate, registry)).toBeNull();
  });

  it("rejects an extension parser getter without invoking it", () => {
    let getterCalls = 0;
    const schema = Object.defineProperty(
      {
        operations: ["custom_operation"],
        completionKinds: ["succeeded"],
        validateResultId: () => true
      },
      "parse",
      {
        enumerable: true,
        get() {
          getterCalls += 1;
          throw new Error("parser getter must not run");
        }
      }
    );
    const registry = { custom_result: schema } as unknown as BillingLookupResultRegistry;

    expect(() => parseBillingOperationLookup(lookupBase(), registry)).not.toThrow();
    expect(parseBillingOperationLookup(lookupBase(), registry)).toBeNull();
    expect(getterCalls).toBe(0);
  });

  it("rejects a polluted extension schema prototype without reading it", () => {
    let getterCalls = 0;
    const prototype = Object.defineProperty({}, "validateContext", {
      get() {
        getterCalls += 1;
        throw new Error("schema prototype getter must not run");
      }
    });
    const schema = Object.assign(Object.create(prototype) as Record<string, unknown>, {
      operations: ["custom_operation"],
      completionKinds: ["succeeded"],
      parse: () => null,
      validateResultId: () => false
    });
    const registry = { custom_result: schema } as unknown as BillingLookupResultRegistry;

    expect(() => parseBillingOperationLookup(lookupBase(), registry)).not.toThrow();
    expect(parseBillingOperationLookup(lookupBase(), registry)).toBeNull();
    expect(getterCalls).toBe(0);
  });
});
