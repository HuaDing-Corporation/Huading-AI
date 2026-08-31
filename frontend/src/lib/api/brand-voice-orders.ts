import { billingHeaders } from "@/lib/api/billing";
import { parseBrandVoiceOrderResource } from "@/lib/api/billing";
import { ApiError, apiFetch } from "@/lib/api/client";
import type { BillingBrandVoiceOrderResource, BillingConfirmation, BillingQuote } from "@/lib/api/types";

export type BrandVoiceOrderStatus = "awaiting_fulfillment" | "fulfilled" | "rejected";
export type BrandVoiceOrderRead = BillingBrandVoiceOrderResource;

export interface BrandVoiceOrderInput {
  order_type: "create" | "renew";
  requested_name: string;
  source_audio_asset_id: string;
  consent_confirmed: true;
  existing_brand_voice_id: string | null;
}

export interface BrandVoiceOrderPage {
  items: BrandVoiceOrderRead[];
  total: number;
  page?: number | null;
  page_size?: number | null;
}

function invalidResponse(): never {
  throw new ApiError("品牌音色订单响应不符合契约。", "INVALID_BRAND_VOICE_ORDER_RESPONSE", 502);
}

function parseOrder(value: unknown): BrandVoiceOrderRead {
  return parseBrandVoiceOrderResource(value) ?? invalidResponse();
}

export function estimateBrandVoiceOrder(input: BrandVoiceOrderInput): Promise<BillingQuote> {
  return apiFetch<BillingQuote>("/api/v1/brand-voice-orders/estimate", { method: "POST", body: input });
}

export async function createBrandVoiceOrder(input: BrandVoiceOrderInput, confirmation: BillingConfirmation): Promise<BrandVoiceOrderRead> {
  const value = await apiFetch<unknown>("/api/v1/brand-voice-orders", {
    method: "POST",
    body: input,
    headers: billingHeaders(confirmation)
  });
  return parseOrder(value);
}

export async function listBrandVoiceOrders(): Promise<BrandVoiceOrderRead[]> {
  const page = await apiFetch<unknown>("/api/v1/brand-voice-orders", { method: "GET" });
  if (
    typeof page !== "object" || page === null || Array.isArray(page) ||
    Object.keys(page).length !== 4 ||
    !["items", "total", "page", "page_size"].every((key) => Object.prototype.hasOwnProperty.call(page, key))
  ) invalidResponse();
  const candidate = page as Record<string, unknown>;
  if (!Array.isArray(candidate.items) || !Number.isSafeInteger(candidate.total) || (candidate.total as number) < 0) invalidResponse();
  if (candidate.page !== null && (!Number.isSafeInteger(candidate.page) || (candidate.page as number) < 1)) invalidResponse();
  if (candidate.page_size !== null && (!Number.isSafeInteger(candidate.page_size) || (candidate.page_size as number) < 1)) invalidResponse();
  return candidate.items.map(parseOrder);
}

export async function getBrandVoiceOrder(orderId: string): Promise<BrandVoiceOrderRead> {
  return parseOrder(await apiFetch<unknown>(`/api/v1/brand-voice-orders/${encodeURIComponent(orderId)}`, { method: "GET" }));
}
