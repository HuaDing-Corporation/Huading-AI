import { apiFetch, multipartFetch } from "@/lib/api/client";
import type {
  AudioUploadResponse,
  BrandVoice,
  BrandVoiceCreateBody,
  BrandVoiceListResponse,
  CreateBrandVoiceInput,
  DeleteResult
} from "@/lib/api/types";

/**
 * 品牌音色 / 声音克隆 (BRAND-VOICE-UI-0001，FIX1 对齐后端 §8) 数据层。
 * 创建为三段式：① 音频 → POST /uploads/audio(multipart file) 取 asset_id；② JSON POST /brand-voices
 * { name, source_audio_asset_id, consent_confirmed:true, provider }（extra=forbid，consent 非 true → 422）。
 * 列表/删除走 apiFetch(JSON)。音频上传复用 client.multipartFetch（鉴权/401/封套单一实现，零裸 fetch）。
 *
 * ⚠️ provider（范围4，克隆通路 doubao/cosyvoice；**缺省 doubao**＝后端 canonical 默认＝现状）**硬依赖后端
 * COSYVOICE-CLONE-0001 合并**：develop 现 BrandVoiceCreateRequest 仍 extra=forbid 且无 provider 字段，收到
 * 任何带 provider 的请求（含缺省 doubao）都会 422 → 创建 100% 不可用。**故 provider 相关改动切勿先于后端上
 * 生产**；本地/CI mock 已收，真接口联调待 COSYVOICE-CLONE-0001 合并后补做。
 */

/** 品牌音色列表（status: processing/ready/failed；处理中前端轮询）。 */
export async function listBrandVoices(): Promise<BrandVoice[]> {
  const res = await apiFetch<BrandVoiceListResponse>("/api/v1/brand-voices", { method: "GET" });
  return res?.items ?? [];
}

/** ① 上传待克隆音频 → { asset_id }（asset type=audio）。 */
export function uploadAudio(audio: Blob): Promise<AudioUploadResponse> {
  const form = new FormData();
  form.append("file", audio);
  return multipartFetch<AudioUploadResponse>("/api/v1/uploads/audio", form, {
    defaultErrorMessage: "音频上传失败",
    defaultErrorCode: "AUDIO_UPLOAD_ERROR"
  });
}

/**
 * ② JSON 创建品牌音色（consent_confirmed + provider 必须进 body）。
 * ⚠️ provider 依赖 COSYVOICE-CLONE-0001 合并——develop extra=forbid 收到 provider 直接 422，勿先于后端上 prod。
 */
export function createBrandVoice(body: BrandVoiceCreateBody): Promise<BrandVoice> {
  return apiFetch<BrandVoice>("/api/v1/brand-voices", { method: "POST", body });
}

/**
 * 三段式编排：上传音频拿 asset_id → JSON 创建（带 consent_confirmed + provider 通路）。
 * ⚠️ provider 依赖 COSYVOICE-CLONE-0001 合并（见模块头注）：develop 现收 provider 会 422，勿先于后端上 prod。
 */
export async function createBrandVoiceFromAudio({ name, audio, consentConfirmed, provider }: CreateBrandVoiceInput): Promise<BrandVoice> {
  const { asset_id } = await uploadAudio(audio);
  return createBrandVoice({ name, source_audio_asset_id: asset_id, consent_confirmed: consentConfirmed, provider });
}

/** 删除品牌音色。 */
export function deleteBrandVoice(id: string): Promise<DeleteResult> {
  return apiFetch<DeleteResult>(`/api/v1/brand-voices/${id}`, { method: "DELETE" });
}
