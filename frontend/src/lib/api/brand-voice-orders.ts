import { billingHeaders } from "@/lib/api/billing";
import { apiFetch } from "@/lib/api/client";
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

export function estimateBrandVoiceOrder(input: BrandVoiceOrderInput): Promise<BillingQuote> {
  return apiFetch<BillingQuote>("/api/v1/brand-voice-orders/estimate", { method: "POST", body: input });
}

export function createBrandVoiceOrder(input: BrandVoiceOrderInput, confirmation: BillingConfirmation): Promise<BrandVoiceOrderRead> {
  return apiFetch<BrandVoiceOrderRead>("/api/v1/brand-voice-orders", {
    method: "POST",
    body: input,
    headers: billingHeaders(confirmation)
  });
}

export async function listBrandVoiceOrders(): Promise<BrandVoiceOrderRead[]> {
  const page = await apiFetch<BrandVoiceOrderPage>("/api/v1/brand-voice-orders", { method: "GET" });
  return page.items;
}

export function getBrandVoiceOrder(orderId: string): Promise<BrandVoiceOrderRead> {
  return apiFetch<BrandVoiceOrderRead>(`/api/v1/brand-voice-orders/${encodeURIComponent(orderId)}`, { method: "GET" });
}
