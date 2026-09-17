import { describe, expect, it } from "vitest";
import { createVideo, estimateVideo, getVideo, listVideosPage, streamVideoEvents } from "@/lib/api/videos";
import { getBillingOperation } from "@/lib/api/billing";
import type { VideoEvent } from "@/lib/api/types";

// Exercise real FE transport and MSW. Snapshot is read-only, never in POST input.
describe("HeyGen mock list/detail snapshots", () => {
  it.each(["v-zhixing", "bv-ready-2"])("explicit mock trusted-duration failure stays failed/settled on REST/SSE/replay: %s", async (voice) => {
    localStorage.setItem("hd_mock_avatar_145_failure", "1");
    try {
      const body = { topic: "145 fixture", script: "四字文案", voice_id: voice, avatar_asset_id: "photo-fixture" };
      const quote = await estimateVideo(body, voice === "bv-ready-2" ? { voice_kind: "brand", voice_provider: "cosyvoice" } : null);
      if (quote.pricing_contract === "deferred_unpriced") throw new Error("unexpected pricing");
      const request = { ...body, avatar_duration_policy: "145s-no-refund-v1" as const, avatar_duration_policy_token: quote.avatar_duration_policy!.token };
      const confirmation = quote.pricing_contract === "billing_quote"
        ? { quote_token: quote.quote_token, idempotency_key: crypto.randomUUID() } : undefined;
      const accepted = await createVideo(request, confirmation);
      const events: VideoEvent[] = [];
      await streamVideoEvents(accepted.id, (event) => events.push(event));
      const credits = quote.pricing_contract === "billing_quote" ? quote.payable_credits : quote.estimated_credits;
      const expected = { status: "failed", error_code: "HEYGEN_AUDIO_DURATION_EXCEEDED", billing_outcome: {
        completion_kind: "failed_charged", status: "settled", policy_version: "145s-no-refund-v1",
        requested_credits: credits, settled_credits: credits, released_credits: 0
      } };
      expect(await getVideo(accepted.id)).toMatchObject(expected);
      expect((await listVideosPage({ mode: "avatar_talk" })).items.find((item) => item.id === accepted.id)).toMatchObject(expected);
      expect(events.at(-1)).toMatchObject(expected);
      expect(events.some((event) => event.status === "done")).toBe(false);
      if (confirmation) {
        expect(await getBillingOperation("video_create", confirmation.idempotency_key)).toMatchObject({ state: "completed",
          completion_kind: "failed_charged", resource: { task_id: accepted.id, status: "failed" }, result: null,
          billing: { status: "settled", settled_credits: credits, released_credits: 0 } });
        const before = (await listVideosPage()).total;
        await expect(createVideo(request, confirmation)).rejects.toMatchObject({ code: "HEYGEN_AUDIO_DURATION_EXCEEDED" });
        expect((await listVideosPage()).total).toBe(before);
      }
    } finally { localStorage.removeItem("hd_mock_avatar_145_failure"); }
  });
  it.each([
    [{ avatar_asset_id: "photo-fixture" }, "avatar_iv"],
    [{ avatar_video_asset_id: "video-fixture" }, "lipsync_precision"]
  ] as const)("%j preserves voice and reads %s from both response surfaces", async (source, model) => {
    const request = { topic: "mock口播", script: "测试文案", voice_id: "v-zhixing", ...source };
    const quote = await estimateVideo(request);
    expect(quote.pricing_contract).toBe("legacy_estimate");
    if (quote.pricing_contract !== "legacy_estimate") throw new Error("unexpected quote");
    expect(quote.avatar_duration_policy?.max_seconds).toBe(145);
    const result = await createVideo({ ...request, avatar_duration_policy: "145s-no-refund-v1",
      avatar_duration_policy_token: quote.avatar_duration_policy!.token });
    const detail = await getVideo(result.id);
    const list = await listVideosPage({ mode: "avatar_talk" });
    expect(detail.avatar_provider).toBe("heygen");
    expect(detail.avatar_model).toBe(model);
    expect(detail.voice_id).toBe("v-zhixing");
    expect(list.items.find((item) => item.id === result.id)).toMatchObject({ avatar_provider: "heygen", avatar_model: model });
  });

  it.each([{ avatar_asset_id: "photo-fixture" }, { avatar_video_asset_id: "video-fixture" }])(
    "rejects missing or changed policy before creating a task: %j", async (source) => {
      const request = { topic: "policy-guard", script: "测试", voice_id: "v-zhixing", ...source };
      const before = await listVideosPage({ mode: "avatar_talk" });
      await expect(createVideo(request)).rejects.toMatchObject({ code: "AVATAR_DURATION_POLICY_REQUIRED", status: 422 });
      const quote = await estimateVideo(request);
      if (quote.pricing_contract !== "legacy_estimate") throw new Error("unexpected quote");
      const accepted = { ...request, avatar_duration_policy: "145s-no-refund-v1" as const,
        avatar_duration_policy_token: quote.avatar_duration_policy!.token };
      await expect(createVideo({ ...accepted, script: "changed" })).rejects.toMatchObject({ code: "AVATAR_DURATION_POLICY_INVALID", status: 422 });
      await expect(createVideo({ ...accepted, avatar_duration_policy_token: "invalid" })).rejects.toMatchObject({ code: "AVATAR_DURATION_POLICY_INVALID", status: 422 });
      expect((await listVideosPage({ mode: "avatar_talk" })).total).toBe(before.total);
    }
  );
});
