import { apiFetch, multipartFetch } from "@/lib/api/client";
import type { BrandVoice, BrandVoiceListResponse, CreateBrandVoiceInput, DeleteResult } from "@/lib/api/types";

/**
 * 品牌音色 / 声音克隆 (BRAND-VOICE-UI-0001) 数据层。列表/删除走 apiFetch(JSON)；创建含音频 Blob
 * 故走 multipartFetch(FormData，复用 client 的鉴权/401/封套实现，不重复造)。契约据 seam §5 推断，
 * 待对冻结 seam 校验。
 */

/** 品牌音色列表（status: processing/ready/failed；处理中前端轮询）。 */
export async function listBrandVoices(): Promise<BrandVoice[]> {
  const res = await apiFetch<BrandVoiceListResponse>("/api/v1/brand-voices", { method: "GET" });
  return res?.items ?? [];
}

/** 创建品牌音色 → multipart(name + audio) → 返回 processing 记录。 */
export function createBrandVoice({ name, audio }: CreateBrandVoiceInput): Promise<BrandVoice> {
  const form = new FormData();
  form.append("name", name);
  form.append("audio", audio);
  return multipartFetch<BrandVoice>("/api/v1/brand-voices", form, {
    defaultErrorMessage: "创建失败",
    defaultErrorCode: "BRAND_VOICE_CREATE_ERROR"
  });
}

/** 删除品牌音色。 */
export function deleteBrandVoice(id: string): Promise<DeleteResult> {
  return apiFetch<DeleteResult>(`/api/v1/brand-voices/${id}`, { method: "DELETE" });
}
