import { billingHeaders } from "@/lib/api/billing";
import { apiFetch, multipartFetch } from "@/lib/api/client";
import type {
  AudioUploadResponse,
  BillingConfirmation,
  BillingQuote,
  BillingSummary,
  BrandVoice,
  BrandVoiceCreateBody,
  BrandVoiceListResponse,
  DeleteResult
} from "@/lib/api/types";

export interface BrandVoiceCreateResponse extends BrandVoice {
  billing: BillingSummary;
}

export async function listBrandVoices(): Promise<BrandVoice[]> {
  const response = await apiFetch<BrandVoiceListResponse>("/api/v1/brand-voices", { method: "GET" });
  return response.items;
}

export function uploadAudio(audio: Blob): Promise<AudioUploadResponse> {
  const form = new FormData();
  form.append("file", audio);
  return multipartFetch<AudioUploadResponse>("/api/v1/uploads/audio", form, {
    defaultErrorMessage: "音频上传失败",
    defaultErrorCode: "AUDIO_UPLOAD_ERROR"
  });
}

export function estimateBrandVoice(body: BrandVoiceCreateBody): Promise<BillingQuote> {
  return apiFetch<BillingQuote>("/api/v1/brand-voices/estimate", { method: "POST", body });
}

export function createBrandVoice(
  body: BrandVoiceCreateBody,
  confirmation: BillingConfirmation
): Promise<BrandVoiceCreateResponse> {
  return apiFetch<BrandVoiceCreateResponse>("/api/v1/brand-voices", {
    method: "POST",
    body,
    headers: billingHeaders(confirmation)
  });
}

export function deleteBrandVoice(id: string): Promise<DeleteResult> {
  return apiFetch<DeleteResult>(`/api/v1/brand-voices/${encodeURIComponent(id)}`, { method: "DELETE" });
}
