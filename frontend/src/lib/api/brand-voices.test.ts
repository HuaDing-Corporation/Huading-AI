import { afterEach, describe, expect, it } from "vitest";

import { createBrandVoiceOrder, estimateBrandVoiceOrder, getBrandVoiceOrder, listBrandVoiceOrders } from "./brand-voice-orders";
import { createBrandVoice, estimateBrandVoice, listBrandVoices, uploadAudio } from "./brand-voices";
import { getQuota } from "./quota";

afterEach(() => localStorage.removeItem("hd_mock_active_subscription"));

describe("brand voices billed API", () => {
  it("uploads, receives a server disclosure, and submits CosyVoice with the same quote", async () => {
    const uploaded = await uploadAudio(new Blob(["audio"], { type: "audio/webm" }));
    const body = { name: "租户音色", source_audio_asset_id: uploaded.asset_id, consent_confirmed: true, provider: "cosyvoice" as const };
    const quote = await estimateBrandVoice(body);
    expect(quote.operation).toBe("cosyvoice_brand_voice_create");
    expect(quote.disclosures[0].rendered_text).toContain("0.2 积分/字");
    const created = await createBrandVoice(body, {
      quote_token: quote.quote_token,
      idempotency_key: "00000000-0000-4000-8000-000000000099"
    });
    expect(created.delivery_status).toBe("active");
    expect(created.billing.status).toBe("settled");
    expect(created.billing.settled_credits).toBe(0);
  });

  it("lists only server-authorized records with rights-aware delivery fields", async () => {
    const items = await listBrandVoices();
    expect(items.length).toBeGreaterThan(0);
    expect(items.every((item) => item.delivery_status && item.provider)).toBe(true);
    expect(items.some((item) => item.name === "平台官方音色")).toBe(false);
    expect(items.some((item) => item.name === "他人购买音色")).toBe(false);
  });

  it("quotes and creates a payer-scoped manual Doubao order with reserved billing", async () => {
    const body = { order_type: "create" as const, requested_name: "新主播音", source_audio_asset_id: "audio-new", consent_confirmed: true as const, existing_brand_voice_id: null };
    const quote = await estimateBrandVoiceOrder(body);
    expect(quote).toMatchObject({ operation: "doubao_brand_voice_order_create", payable_credits: 30000 });
    const created = await createBrandVoiceOrder(body, { quote_token: quote.quote_token, idempotency_key: "00000000-0000-4000-8000-000000000098" });
    expect(created).toMatchObject({ status: "awaiting_fulfillment", expires_at: null, billing: { status: "reserved", held_credits: 30000 } });
    const listed = await listBrandVoiceOrders();
    expect(listed.some((item) => item.id === created.id)).toBe(true);
    expect(listed.some((item) => item.id === "bvo-other-user")).toBe(false);
  });

  it("exposes every server-authoritative refund disposition and applies a pending grant on re-query", async () => {
    expect((await getBrandVoiceOrder("bvo-released")).refund_disposition).toBe("source_subscription_released");
    expect((await getBrandVoiceOrder("bvo-credited")).refund_disposition).toBe("current_subscription_credited");
    const pending = await getBrandVoiceOrder("bvo-pending");
    expect(pending).toMatchObject({ refund_disposition: "pending_next_subscription", refund_grant_status: "pending", refund_applied_at: null });
    const applied = await getBrandVoiceOrder("bvo-pending");
    expect(applied).toMatchObject({ refund_disposition: "current_subscription_credited", refund_grant_status: "applied", refund_applied_at: "2026-09-01T00:00:00Z" });
  });

  it("returns the exact quota schema and a zero wallet without an active subscription", async () => {
    expect(await getQuota()).toEqual({ has_active_subscription: true, active_subscription_id: "sub-mock", total: 1000, used: 120, reserved: 36, remaining: 844, manual_fulfillment_held_credits: 30000, pending_refund_credits: 0 });
    localStorage.setItem("hd_mock_active_subscription", "0");
    expect(await getQuota()).toEqual({ has_active_subscription: false, active_subscription_id: null, total: 0, used: 0, reserved: 0, remaining: 0, manual_fulfillment_held_credits: 0, pending_refund_credits: 30000 });
  });
});
