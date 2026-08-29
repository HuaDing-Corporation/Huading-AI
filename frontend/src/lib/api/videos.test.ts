import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "@/mocks/server";

import { getBillingOperation } from "@/lib/api/billing";
import {
  createVideo,
  estimateScenePrompt,
  estimateVideo,
  generateScenePrompt,
  videoPricingContextForVoice
} from "@/lib/api/videos";

const API = "http://localhost:8000";
const sceneConfirmation = {
  quote_token: "scene-quote-token",
  idempotency_key: "11111111-1111-4111-8111-111111111111"
};

const videoConfirmation = {
  quote_token: "video-quote-token",
  idempotency_key: "22222222-2222-4222-8222-222222222222"
};

let sceneRequestSequence = 0;

async function confirmedScenePrompt(input: Parameters<typeof estimateScenePrompt>[0]) {
  sceneRequestSequence += 1;
  const quote = await estimateScenePrompt(input);
  return generateScenePrompt(input, {
    quote_token: quote.quote_token,
    idempotency_key: `30000000-0000-4000-8000-${String(sceneRequestSequence).padStart(12, "0")}`
  });
}

const videoQuote = {
  pricing_contract: "billing_quote" as const,
  pricing_shape: "composite" as const,
  operation: "video_create",
  unit: null,
  quantity: null,
  unit_credits: null,
  rate_scope: null,
  rate_source: null,
  subtotal_credits: "100.0000",
  payable_credits: 100,
  breakdown: [
    {
      operation: "video_create",
      capability: "video",
      unit: "second",
      quantity: "1",
      unit_credits: "100.0000",
      subtotal_credits: "100.0000",
      rate_scope: "tenant_overridable" as const,
      rate_source: "code_default" as const,
      rate_id: null,
      effective_at: null,
      policy_key: "video_create",
      policy_version: 1,
      label: "视频生成"
    }
  ] as const,
  disclosures: [],
  quote_token: videoConfirmation.quote_token,
  expires_at: "2030-01-01T00:00:00Z"
};

const videoBilling = {
  operation_id: "video-operation",
  idempotency_key: videoConfirmation.idempotency_key,
  status: "reserved" as const,
  requested_credits: 100,
  held_credits: 100,
  settled_credits: 0,
  released_credits: 0
};

describe("videoPricingContextForVoice", () => {
  const voice = {
    id: "standard-1",
    provider: "edge_tts",
    voice_code: "standard-1",
    display_name: "标准音色",
    gender: null,
    language: null
  };
  const brand = (provider: string | null) => ({
    id: "brand-1",
    name: "品牌音色",
    status: "ready" as const,
    created_at: "2026-08-29T00:00:00Z",
    provider
  });

  it("derives canonical context from the selected records and fails closed for an unknown brand provider", () => {
    expect(videoPricingContextForVoice("brand-1", [voice], [brand("cosyvoice-voice-clone")]))
      .toEqual({ voice_kind: "brand", voice_provider: "cosyvoice" });
    expect(videoPricingContextForVoice("brand-1", [voice], [brand("doubao")]))
      .toEqual({ voice_kind: "brand", voice_provider: "doubao" });
    expect(videoPricingContextForVoice("standard-1", [voice], []))
      .toEqual({ voice_kind: "standard", voice_provider: "edge_tts" });
    expect(videoPricingContextForVoice("brand-1", [voice], [brand(null)])).toBeNull();
  });
});

// ECOM-VIDEO-OPTIMIZE-UI-0001 · FIX2 · P1：确认窗打开必调 POST /videos/estimate。此前缺 mock handler →
// MSW 放行到真后端 → CI net::ERR_FAILED（#203 红）。本测真走 apiFetch → 全局 MSW（vitest.setup 已 server.listen），
// 非 stub：验响应契约形状（对齐 #202 VideoEstimateResponse）+ 防假绿（estimate 与提交同一 VideoGenerateRequest 校验，
// 缺产品图/音色在 estimate 阶段即 422，mock 不比 BE 宽松）。
describe("estimateVideo · POST /videos/estimate（apiFetch 真走 MSW · FIX2 P1）", () => {
  it("default MSW quotes and stores a confirmed CosyVoice branded video operation", async () => {
    const input = {
      video_mode: "avatar_talk",
      topic: "品牌口播",
      script: "四字文案",
      voice_id: "bv-ready-2",
      avatar_asset_id: "avatar-1"
    };
    const quote = await estimateVideo(input, {
      voice_kind: "brand",
      voice_provider: "cosyvoice"
    });
    expect(quote).toMatchObject({
      pricing_contract: "billing_quote",
      pricing_shape: "composite",
      operation: "video_create",
      breakdown: [
        { capability: "video", unit: "second" },
        {
          operation: "cosyvoice_brand_tts",
          capability: "tts",
          unit: "character",
          quantity: "4",
          unit_credits: "0.1000"
        }
      ]
    });
    if (quote.pricing_contract !== "billing_quote") throw new Error("expected billed video quote");
    const key = "55555555-5555-4555-8555-555555555555";
    const accepted = await createVideo(input, {
      quote_token: quote.quote_token,
      idempotency_key: key
    });
    expect(accepted).toMatchObject({
      pricing_contract: "billing_quote",
      status: "queued",
      billing: { idempotency_key: key, status: "reserved" }
    });
    const lookup = await getBillingOperation("video_create", key);
    expect(lookup).toMatchObject({
      operation: "video_create",
      state: "in_progress",
      completion_kind: null,
      result_type: "video_task",
      result_id: accepted.id,
      resource: { task_id: accepted.id, status: "queued" }
    });
  });

  it("default MSW keeps a Doubao branded quote free of CosyVoice character pricing", async () => {
    const quote = await estimateVideo(
      {
        video_mode: "avatar_talk",
        topic: "豆包品牌口播",
        script: "真实文案",
        voice_id: "bv-ready-1",
        avatar_asset_id: "avatar-1"
      },
      { voice_kind: "brand", voice_provider: "doubao" }
    );
    expect(quote).toMatchObject({
      pricing_contract: "billing_quote",
      pricing_shape: "composite",
      operation: "video_create"
    });
    if (quote.pricing_contract !== "billing_quote" || quote.pricing_shape !== "composite") {
      throw new Error("expected billed Doubao video quote");
    }
    expect(quote.breakdown).toHaveLength(1);
    expect(quote.breakdown[0]).toMatchObject({ capability: "video", unit: "second" });
    expect(quote.breakdown.some((line) => line.unit === "character")).toBe(false);
  });

  it("default MSW requires billable text and confirmation only for branded videos", async () => {
    const branded = {
      video_mode: "avatar_talk",
      topic: "品牌口播",
      voice_id: "bv-ready-2",
      avatar_asset_id: "avatar-1"
    };
    await expect(estimateVideo(branded, {
      voice_kind: "brand",
      voice_provider: "cosyvoice"
    })).rejects.toMatchObject({
      code: "BILLABLE_TEXT_REQUIRED",
      status: 422
    });
    await expect(createVideo({ ...branded, script: "已有文案" })).rejects.toMatchObject({
      code: "BILLING_HEADERS_REQUIRED",
      status: 422
    });
  });

  it("default MSW returns deferred and legacy contracts without honoring forged billing headers", async () => {
    await expect(estimateVideo({ video_mode: "static_template", topic: "延期" })).resolves.toEqual({
      pricing_contract: "deferred_unpriced",
      estimated_credits: 0,
      unit: "credits",
      unpriced: true,
      note: "延期处理／尚未闭环"
    });
    for (const [body, pricingContract] of [
      [{ video_mode: "static_template", topic: "延期" }, "deferred_unpriced"],
      [{ video_mode: "photo", topic: "旧计费图片" }, "legacy_estimate"]
    ] as const) {
      const response = await fetch(`${API}/api/v1/videos`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": "66666666-6666-4666-8666-666666666666",
          "X-Huading-Quote": "forged-quote"
        },
        body: JSON.stringify(body)
      });
      const envelope = (await response.json()) as { data: Record<string, unknown> };
      expect(envelope.data).toMatchObject({ pricing_contract: pricingContract, status: "queued" });
      expect(envelope.data).not.toHaveProperty("billing");
    }
  });

  it("fails closed when a billing_quote estimate is missing its signed quote token", async () => {
    server.use(
      http.post(`${API}/api/v1/videos/estimate`, () =>
        HttpResponse.json({
          data: {
            pricing_contract: "billing_quote",
            operation: "video_create",
            payable_credits: 100
          },
          error: null,
          request_id: "malformed"
        })
      )
    );
    await expect(
      estimateVideo({
        video_mode: "avatar_talk",
        voice_id: "brand-voice",
        avatar_asset_id: "avatar-1",
        script: "测试口播"
      })
    ).rejects.toMatchObject({ code: "INVALID_VIDEO_PRICING_CONTRACT" });
  });

  it("parses billing_quote, legacy_estimate and deferred_unpriced as mutually exclusive contracts", async () => {
    server.use(
      http.post(`${API}/api/v1/videos/estimate`, async ({ request }) => {
        const body = (await request.json()) as { topic?: string };
        if (body.topic === "billing") return HttpResponse.json({ data: videoQuote, error: null, request_id: "quote" });
        if (body.topic === "legacy") {
          return HttpResponse.json({
            data: {
              pricing_contract: "legacy_estimate",
              estimated_credits: 12,
              unit: "credits",
              note: "旧计费流程"
            },
            error: null,
            request_id: "legacy"
          });
        }
        return HttpResponse.json({
          data: {
            pricing_contract: "deferred_unpriced",
            estimated_credits: 0,
            unit: "credits",
            unpriced: true,
            note: "延期处理／尚未闭环"
          },
          error: null,
          request_id: "deferred"
        });
      })
    );

    await expect(
      estimateVideo(
        { topic: "billing", voice_id: "brand-voice" },
        { voice_kind: "brand", voice_provider: "doubao" }
      )
    ).resolves.toEqual(videoQuote);
    await expect(estimateVideo({ topic: "legacy" })).resolves.toMatchObject({
      pricing_contract: "legacy_estimate",
      estimated_credits: 12
    });
    await expect(estimateVideo({ topic: "deferred" })).resolves.toEqual({
      pricing_contract: "deferred_unpriced",
      estimated_credits: 0,
      unit: "credits",
      unpriced: true,
      note: "延期处理／尚未闭环"
    });
  });

  it("rejects a deferred response that invents a reason_code", async () => {
    server.use(
      http.post(`${API}/api/v1/videos/estimate`, () =>
        HttpResponse.json({
          data: {
            pricing_contract: "deferred_unpriced",
            estimated_credits: 0,
            unit: "credits",
            unpriced: true,
            reason_code: "NOT_YET_PRICED"
          },
          error: null,
          request_id: "malformed-deferred"
        })
      )
    );
    await expect(estimateVideo({ topic: "deferred" })).rejects.toMatchObject({
      code: "INVALID_VIDEO_PRICING_CONTRACT"
    });
  });

  it("seedance_i2v 合法体 → 返回契约形状 {estimated_credits:number, unit:'credits', note}", async () => {
    const res = await estimateVideo({
      video_mode: "seedance_i2v",
      product_image_keys: ["uploads/mock-product-1.png"],
      voice_id: "v1",
      duration_sec: 30,
      resolution: "720p"
    });
    expect(res.pricing_contract).toBe("legacy_estimate");
    if (res.pricing_contract !== "legacy_estimate") throw new Error("expected legacy estimate");
    expect(res.unit).toBe("credits");
    expect(typeof res.estimated_credits).toBe("number");
    expect(res.estimated_credits).toBeGreaterThan(0);
    expect(res.note).toBeTruthy(); // #202 _ESTIMATE_NOTE
  });

  it("防假绿：seedance_i2v 缺产品图 → estimate 阶段即 422（不放到提交才红）", async () => {
    await expect(
      estimateVideo({ video_mode: "seedance_i2v", voice_id: "v1", duration_sec: 30 })
    ).rejects.toThrow();
  });

  it("防假绿：seedance_i2v 缺音色 → 422", async () => {
    await expect(
      estimateVideo({ video_mode: "seedance_i2v", product_image_keys: ["uploads/x.png"], duration_sec: 30 })
    ).rejects.toThrow();
  });

  // FIX1（CB P1 · 提交/预估路径）：VideoGenerateRequest.duration_sec 也是 int，小数 → 422。
  it("防假绿：estimate 小数 duration_sec(5.5) → 422", async () => {
    await expect(
      estimateVideo({ video_mode: "seedance_i2v", product_image_keys: ["uploads/x.png"], voice_id: "v1", duration_sec: 5.5 })
    ).rejects.toThrow();
  });
});

describe("createVideo · strict accepted pricing contract", () => {
  it("sends exact billing headers only for a confirmed billed submit", async () => {
    const calls: Array<{ quote: string | null; key: string | null }> = [];
    server.use(
      http.post(`${API}/api/v1/videos`, ({ request }) => {
        const quote = request.headers.get("X-Huading-Quote");
        const key = request.headers.get("Idempotency-Key");
        calls.push({ quote, key });
        const id = `task-${calls.length}`;
        return HttpResponse.json({
          data: quote
            ? {
                id,
                task_id: id,
                status: "queued",
                pricing_contract: "billing_quote",
                billing: videoBilling
              }
            : {
                id,
                task_id: id,
                status: "queued",
                pricing_contract: "legacy_estimate"
              },
          error: null,
          request_id: id
        }, { status: 202 });
      })
    );

    await expect(createVideo({ topic: "billing" }, videoConfirmation)).resolves.toMatchObject({
      pricing_contract: "billing_quote",
      billing: videoBilling
    });
    await expect(createVideo({ topic: "legacy" })).resolves.toMatchObject({
      pricing_contract: "legacy_estimate"
    });
    expect(calls).toEqual([
      { quote: videoConfirmation.quote_token, key: videoConfirmation.idempotency_key },
      { quote: null, key: null }
    ]);
  });

  it.each([
    {
      pricing_contract: "billing_quote",
      id: "task-billed",
      task_id: "task-billed",
      status: "queued"
    },
    {
      pricing_contract: "legacy_estimate",
      id: "task-legacy",
      task_id: "task-legacy",
      status: "queued",
      billing: videoBilling
    }
  ])("fails closed when accepted branch fields do not match $pricing_contract", async (data) => {
    server.use(
      http.post(`${API}/api/v1/videos`, () =>
        HttpResponse.json({ data, error: null, request_id: "malformed-accepted" }, { status: 202 })
      )
    );
    await expect(createVideo({ topic: "bad" })).rejects.toMatchObject({
      code: "INVALID_VIDEO_ACCEPTED_CONTRACT"
    });
  });
});

// ECOM-VIDEO-SCENE-DURATION-FIX-UI-0001：scene-prompt 补传 duration_sec（Cowork 冻结 §4.2 漏了它，致秒数恒「约15秒」）。
// 真走 apiFetch→MSW，验 ①duration 透传后 mock scene_prompt 反映该时长；②mock 夹取 [5,120] 镜像 BE _clamp_duration（不 reject）。
describe("generateScenePrompt · duration 透传（apiFetch 真走 MSW · SCENE-DURATION-FIX）", () => {
  it("estimates first and sends the exact billing confirmation headers", async () => {
    const submit = vi.fn();
    const input = {
      topic: "保温杯",
      product_image_keys: ["uploads/mock-product-1.png"],
      duration_sec: 10
    };
    server.use(
      http.post(`${API}/api/v1/videos/scene-prompt/estimate`, async ({ request }) => {
        expect(await request.json()).toEqual(input);
        return HttpResponse.json({
          data: {
            pricing_contract: "billing_quote",
            pricing_shape: "simple",
            operation: "scene_prompt",
            unit: "request",
            quantity: "1",
            unit_credits: "30",
            subtotal_credits: "30",
            payable_credits: 30,
            rate_scope: "platform_fixed",
            rate_source: "fixed_policy",
            breakdown: [],
            disclosures: [],
            quote_token: sceneConfirmation.quote_token,
            expires_at: new Date(Date.now() + 60_000).toISOString()
          },
          error: null,
          request_id: "scene-estimate"
        });
      }),
      http.post(`${API}/api/v1/videos/scene-prompt`, async ({ request }) => {
        submit({
          body: await request.json(),
          quote: request.headers.get("X-Huading-Quote"),
          key: request.headers.get("Idempotency-Key")
        });
        return HttpResponse.json({
          data: {
            scene_prompt: "服务端画面",
            negative_prompt: "模糊",
            billing: {
              operation_id: "scene-operation",
              idempotency_key: sceneConfirmation.idempotency_key,
              status: "settled",
              requested_credits: 30,
              held_credits: 0,
              settled_credits: 30,
              released_credits: 0
            }
          },
          error: null,
          request_id: "scene-submit"
        });
      })
    );

    await expect(estimateScenePrompt(input)).resolves.toMatchObject({
      operation: "scene_prompt",
      payable_credits: 30
    });
    await expect(generateScenePrompt(input, sceneConfirmation)).resolves.toMatchObject({
      scene_prompt: "服务端画面",
      billing: { status: "settled" }
    });
    expect(submit).toHaveBeenCalledWith({
      body: input,
      quote: sceneConfirmation.quote_token,
      key: sceneConfirmation.idempotency_key
    });
  });

  it("带 duration_sec:10 → scene_prompt 反映「约 10 秒」（秒数随选择变化，非恒 15）", async () => {
    const res = await confirmedScenePrompt({
      product_image_keys: ["uploads/mock-product-1.png"],
      duration_sec: 10
    });
    expect(res.scene_prompt).toContain("约 10 秒");
  });

  it("BE 夹取 [5,120] 镜像：传 3 → 夹到 5；传 200 → 夹到 120（不 reject，mock 不比 BE 宽松）", async () => {
    expect(
      (await confirmedScenePrompt({
        product_image_keys: ["uploads/x.png"],
        duration_sec: 3
      })).scene_prompt
    ).toContain("约 5 秒");
    expect(
      (await confirmedScenePrompt({
        product_image_keys: ["uploads/x.png"],
        duration_sec: 200
      })).scene_prompt
    ).toContain("约 120 秒");
  });

  // FIX1（CB P1）防假绿：真 BE duration_sec 是 int，小数/字符串 → 422（不四舍五入放行；此前 round 5.5→6 是假绿，线上真 422）。
  it("小数 duration_sec(5.5/5.4) → 422（镜像 BE int，不放行）", async () => {
    await expect(estimateScenePrompt({ product_image_keys: ["uploads/x.png"], duration_sec: 5.5 })).rejects.toThrow();
    await expect(estimateScenePrompt({ product_image_keys: ["uploads/x.png"], duration_sec: 5.4 })).rejects.toThrow();
  });

  it("字符串 duration_sec('5.5') → 422（BE int 也拒字符串）", async () => {
    await expect(
      // @ts-expect-error 故意传非法类型：真 BE int 拒字符串，mock 须同样 422（防假绿）
      estimateScenePrompt({ product_image_keys: ["uploads/x.png"], duration_sec: "5.5" })
    ).rejects.toThrow();
  });
});

// IMAGE-GEN-OPTIMIZE-UI-0001 §四：photo 提交 mock 校验（createVideo 真走 MSW）。mock 不比 BE 宽松：image_keys 1–6、
// 四强度 10..100 步10；未开启不出现。⚠️ 零回归：AI 封面(purpose:cover + image_size/image_quality，无 image_keys/强度)天然全过。
// FIX1 真联调：image_keys 须为 BE 格式 uploads/<name>.{jpg,jpeg,png,webp}（POST /uploads 返回值），mock 已对齐收紧。
const refKey = (i: number) => `uploads/ref-${i}.png`;
const sixRefs = Array.from({ length: 6 }, (_, i) => refKey(i));

describe("createVideo · photo 提交校验（apiFetch 真走 MSW · IMAGE-GEN-OPTIMIZE-UI-0001）", () => {
  it("合法 photo（6 张参考图 + 相似度 80）→ 202 accepted", async () => {
    const res = await createVideo({
      topic: "一只橘猫",
      video_mode: "photo",
      image_keys: sixRefs,
      similarity_strength: 80,
      aspect_ratio: "1:1"
    });
    expect(res.status).toBe("queued");
  });

  it("防假绿：image_keys 7 张 → 422（1–6 上限）", async () => {
    await expect(
      createVideo({ topic: "x", video_mode: "photo", image_keys: [...sixRefs, refKey(6)] })
    ).rejects.toThrow();
  });

  // FIX1 真联调：逐字对齐 BE _IMAGE_KEY_RE——非 uploads/*.{jpg,jpeg,png,webp} 的假 key → 422（mock 此前放行「a」是假绿）。
  it("防假绿：image_keys 格式非法（裸「a」非 uploads/*）→ 422", async () => {
    await expect(createVideo({ topic: "x", video_mode: "photo", image_keys: ["a"] })).rejects.toThrow();
    await expect(
      createVideo({ topic: "x", video_mode: "photo", image_keys: ["uploads/x.gif"] })
    ).rejects.toThrow(); // gif 不在 BE 白名单
  });

  // FIX1 真联调：四层提示词各 ≤20000（BE schema 校验）。20001 → 422；20000 → 放行。
  it("防假绿：提示词超 20000 → 422；恰 20000 → 202", async () => {
    await expect(
      createVideo({ topic: "x".repeat(20001), video_mode: "photo" })
    ).rejects.toThrow();
    const ok = await createVideo({ topic: "x".repeat(20000), video_mode: "photo", master_prompt: "y".repeat(20000) });
    expect(ok.status).toBe("queued");
  });

  it("防假绿：强度非步长10（55）→ 422", async () => {
    await expect(createVideo({ topic: "x", video_mode: "photo", subject_strength: 55 })).rejects.toThrow();
  });

  it("防假绿：强度越界（110）→ 422", async () => {
    await expect(createVideo({ topic: "x", video_mode: "photo", creativity_strength: 110 })).rejects.toThrow();
  });

  it("§3之二：合法 image_resolution(2k) → 202；非法(8k) → 422（mock 不比 BE 宽松）", async () => {
    const ok = await createVideo({ topic: "x", video_mode: "photo", image_resolution: "2k" });
    expect(ok.status).toBe("queued");
    await expect(createVideo({ topic: "x", video_mode: "photo", image_resolution: "8k" })).rejects.toThrow();
  });

  it("零回归：AI 封面（purpose:cover + image_size/image_quality，无 image_keys/强度）→ 202 全过", async () => {
    const res = await createVideo({
      topic: "封面",
      video_mode: "photo",
      purpose: "cover",
      image_size: "1024x1536",
      image_quality: "medium"
    });
    expect(res.status).toBe("queued");
  });
});

// VIDEO-GEN-PARAMS-UI-0001 §6：video_gen 提交 mock 校验（createVideo 真走 MSW）。mock 不比 BE 宽松：
// 时长整数 4–15（3/16/20/5.5→422）、提示词 topic≤2000（2001→422）、画面比例 7 值（非法→422）、
// generate_audio/negative_prompt 随请求传（extra=forbid 不误杀）。⚠️ 参考图 1–9 唯一、prompt 非空为既有门。
describe("createVideo · video_gen 提交校验（apiFetch 真走 MSW · VIDEO-GEN-PARAMS-UI-0001）", () => {
  const base = {
    video_mode: "video_gen" as const,
    prompt: "赛博夜景",
    topic: "赛博夜景",
    reference_image_asset_ids: ["a1"]
  };

  it("合法 video_gen（时长8 + 16:9 + 音频开 + 负面）→ 202 queued", async () => {
    const res = await createVideo({
      ...base,
      duration_sec: 8,
      aspect_ratio: "16:9",
      generate_audio: true,
      negative_prompt: "水印、变形"
    });
    expect(res.status).toBe("queued");
  });

  it("防假绿：时长越界/小数（3/16/20/5.5）→ 422（整数 4–15）", async () => {
    for (const d of [3, 16, 20, 5.5]) {
      await expect(createVideo({ ...base, duration_sec: d })).rejects.toThrow();
    }
  });

  it("防假绿：预设边界 4 与 15 合法 → 202；且旧的仅 5/10/15 已放宽", async () => {
    expect((await createVideo({ ...base, duration_sec: 4 })).status).toBe("queued");
    expect((await createVideo({ ...base, duration_sec: 15 })).status).toBe("queued");
  });

  it("防假绿：提示词 topic 超 2000（2001）→ 422；恰 2000 → 202", async () => {
    const s = (n: number) => "文".repeat(n);
    await expect(createVideo({ ...base, prompt: s(2001), topic: s(2001), duration_sec: 8 })).rejects.toThrow();
    expect((await createVideo({ ...base, prompt: s(2000), topic: s(2000), duration_sec: 8 })).status).toBe("queued");
  });

  it("防假绿：画面比例非法（8:1）→ 422；7 值任一（3:4）→ 202", async () => {
    await expect(createVideo({ ...base, duration_sec: 8, aspect_ratio: "8:1" })).rejects.toThrow();
    expect((await createVideo({ ...base, duration_sec: 8, aspect_ratio: "3:4" })).status).toBe("queued");
  });
});

// Code Review（VIDEO-GEN-PARAMS-UI-0001）：estimate 与提交同门——video_gen 非法时长/比例在 estimate 阶段即 422
// （此前 estimate 缺该分支 → estimate 200 而提交 422，estimate 比提交宽松=假绿口）。
describe("estimateVideo · video_gen 同门校验（Code Review 补）", () => {
  it("estimate video_gen 时长 20（越界）→ 422（不放到提交才红）", async () => {
    await expect(
      estimateVideo({ video_mode: "video_gen", duration_sec: 20, resolution: "720p" })
    ).rejects.toThrow();
  });

  it("estimate video_gen 非法比例（2:3）→ 422；合法（8s + adaptive）→ 200", async () => {
    await expect(
      estimateVideo({ video_mode: "video_gen", duration_sec: 8, aspect_ratio: "2:3" })
    ).rejects.toThrow();
    const ok = await estimateVideo({ video_mode: "video_gen", duration_sec: 8, aspect_ratio: "auto" });
    expect(ok.pricing_contract).toBe("legacy_estimate");
    if (ok.pricing_contract !== "legacy_estimate") throw new Error("expected legacy estimate");
    expect(ok.estimated_credits).toBeGreaterThan(0);
  });
});

// Code Review：2000 墙按**码点**计数对齐 BE Python len()——1001 个增补面 emoji（UTF-16 长 2002）按码点是 1001 ≤ 2000，
// 不得误杀；1001+1000 个普通字仍拦。
describe("createVideo · 2000 墙码点计数（Code Review 补）", () => {
  const vg = { video_mode: "video_gen" as const, reference_image_asset_ids: ["a1"], duration_sec: 8 };
  it("1001 个 emoji（UTF-16 2002 码元 / 1001 码点）→ 202 不误杀", async () => {
    const emoji = "😀".repeat(1001);
    const res = await createVideo({ ...vg, prompt: emoji, topic: emoji });
    expect(res.status).toBe("queued");
  });
  it("2001 码点（普通字符）→ 仍 422", async () => {
    const s = "文".repeat(2001);
    await expect(createVideo({ ...vg, prompt: s, topic: s })).rejects.toThrow();
  });
});

// V2V（Code Review 自查·同「estimate 比提交宽松」教训）：estimate 与提交同门——互斥/条数在 estimate 阶段即 422。
describe("estimateVideo · V2V 同门校验（互斥/条数）", () => {
  it("estimate 图+视频同传 → 422；视频 4 条 → 422；合法 2 条 → 200", async () => {
    await expect(
      estimateVideo({ video_mode: "video_gen", duration_sec: 8, reference_image_asset_ids: ["a1"], reference_video_asset_ids: ["v1"] })
    ).rejects.toThrow();
    await expect(
      estimateVideo({ video_mode: "video_gen", duration_sec: 8, reference_video_asset_ids: ["v1", "v2", "v3", "v4"] })
    ).rejects.toThrow();
    const ok = await estimateVideo({ video_mode: "video_gen", duration_sec: 8, reference_video_asset_ids: ["v1", "v2"] });
    expect(ok.pricing_contract).toBe("legacy_estimate");
    if (ok.pricing_contract !== "legacy_estimate") throw new Error("expected legacy estimate");
    expect(ok.estimated_credits).toBeGreaterThan(0);
  });
});
