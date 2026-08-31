import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { API_BASE_URL } from "./client";
import { createBrandVoiceOrder, getBrandVoiceOrder, listBrandVoiceOrders } from "./brand-voice-orders";
import { getAdminBrandVoiceOrder, listAdminBrandVoiceOrders, resolveAdminBrandVoiceOrder } from "./admin-console";
import { server } from "@/mocks/server";

const billing = {
  operation_id: "00000000-0000-4000-8000-100000000001",
  idempotency_key: "00000000-0000-4000-8000-200000000001",
  status: "released",
  requested_credits: 30000,
  held_credits: 0,
  settled_credits: 0,
  released_credits: 30000
};

function rejectedOrder(overrides: Record<string, unknown> = {}) {
  return {
    id: "order-contract",
    tenant_id: "tenant-1",
    ordered_by_user_id: "user-1",
    order_type: "create",
    requested_name: "主播音",
    source_audio_asset_id: "asset-1",
    existing_brand_voice_id: null,
    status: "rejected",
    fulfilled_brand_voice_id: null,
    fulfilled_provider_voice_id: null,
    rejection_reason: "素材不合格",
    fulfilled_at: null,
    expires_at: null,
    rejected_at: "2026-08-30T03:00:00Z",
    created_at: "2026-08-30T01:00:00Z",
    updated_at: "2026-08-30T03:00:00Z",
    billing,
    refund_disposition: "pending_next_subscription",
    refund_grant_status: "pending",
    refund_applied_at: null,
    ...overrides
  };
}

function fulfilledOrder(overrides: Record<string, unknown> = {}) {
  return rejectedOrder({
    status: "fulfilled",
    fulfilled_brand_voice_id: "voice-1",
    fulfilled_provider_voice_id: "S_voice_1",
    rejection_reason: null,
    fulfilled_at: "2026-08-30T03:00:00Z",
    expires_at: "2027-08-30T03:00:00Z",
    rejected_at: null,
    billing: { ...billing, status: "settled", settled_credits: 30000, released_credits: 0 },
    refund_disposition: "not_applicable",
    refund_grant_status: null,
    refund_applied_at: null,
    ...overrides
  });
}

const ok = (data: unknown) => HttpResponse.json({ data, error: null, request_id: "contract-test" });

describe("brand voice order response runtime contract", () => {
  it.each([
    ["malformed refund tuple", rejectedOrder({ refund_grant_status: "applied", refund_applied_at: null })],
    ["extra key", rejectedOrder({ client_price: 30000 })],
    ["billing status inconsistent with rejection", rejectedOrder({ billing: { ...billing, status: "reserved", held_credits: 30000, released_credits: 0 } })],
    ["fulfilled expiry not exactly 365 days", fulfilledOrder({ expires_at: "2027-08-29T03:00:00Z" })]
  ])("rejects a customer detail with %s", async (_label, resource) => {
    server.use(http.get(`${API_BASE_URL}/api/v1/brand-voice-orders/order-contract`, () => ok(resource)));
    await expect(getBrandVoiceOrder("order-contract")).rejects.toMatchObject({ code: "INVALID_BRAND_VOICE_ORDER_RESPONSE" });
  });

  it("rejects one malformed item from the customer list", async () => {
    server.use(http.get(`${API_BASE_URL}/api/v1/brand-voice-orders`, () => ok({ items: [rejectedOrder({ refund_disposition: "not_applicable" })], total: 1, page: null, page_size: null })));
    await expect(listBrandVoiceOrders()).rejects.toMatchObject({ code: "INVALID_BRAND_VOICE_ORDER_RESPONSE" });
  });

  it("rejects a malformed customer create response", async () => {
    server.use(http.post(`${API_BASE_URL}/api/v1/brand-voice-orders`, () => ok(rejectedOrder({ refund_grant_status: "applied" }))));
    await expect(createBrandVoiceOrder({ order_type: "create", requested_name: "主播音", source_audio_asset_id: "asset-1", consent_confirmed: true, existing_brand_voice_id: null }, { quote_token: "quote", idempotency_key: billing.idempotency_key })).rejects.toMatchObject({ code: "INVALID_BRAND_VOICE_ORDER_RESPONSE" });
  });

  it("rejects a malformed admin resolve response through the same contract", async () => {
    server.use(http.post(`${API_BASE_URL}/api/v1/admin/console/brand-voice-orders/order-contract/resolve`, () => ok(rejectedOrder({ refund_applied_at: "2026-09-01T00:00:00Z" }))));
    await expect(resolveAdminBrandVoiceOrder("order-contract", { action: "reject", rejection_reason: "素材不合格" })).rejects.toMatchObject({ code: "INVALID_BRAND_VOICE_ORDER_RESPONSE" });
  });

  it("rejects malformed admin list and detail resources through the same contract", async () => {
    server.use(
      http.get(`${API_BASE_URL}/api/v1/admin/console/brand-voice-orders`, () => ok({ items: [rejectedOrder({ extra: true })], total: 1, page: 1, page_size: 20 })),
      http.get(`${API_BASE_URL}/api/v1/admin/console/brand-voice-orders/order-contract`, () => ok({ ...rejectedOrder({ refund_grant_status: "applied" }), source_audio_url: "https://signed.test/audio" }))
    );
    await expect(listAdminBrandVoiceOrders({ page: 1, page_size: 20 })).rejects.toMatchObject({ code: "INVALID_BRAND_VOICE_ORDER_RESPONSE" });
    await expect(getAdminBrandVoiceOrder("order-contract")).rejects.toMatchObject({ code: "INVALID_BRAND_VOICE_ORDER_RESPONSE" });
  });

  it("accepts the one explicit signed-audio extension on an otherwise exact admin detail", async () => {
    server.use(http.get(`${API_BASE_URL}/api/v1/admin/console/brand-voice-orders/order-contract`, () => ok({ ...rejectedOrder(), source_audio_url: "https://signed.test/audio" })));
    await expect(getAdminBrandVoiceOrder("order-contract")).resolves.toMatchObject({ id: "order-contract", source_audio_url: "https://signed.test/audio" });
  });
});
