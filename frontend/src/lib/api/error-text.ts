import { ApiError } from "@/lib/api/client";
import { copy } from "@/lib/copy";

// 数字人出镜视频源·后端二次校验码 → 友好中文（AVATAR-VIDEO-SOURCE-UI / FE-INTEGRATION-0001）。
// 前端预检拦大部分；BE 权威码(如编码 codec 前端读不到)在生成时回显时走此映射。逐字对齐 routes/videos.py。
const AVATAR_VIDEO_ERROR_COPY: Record<string, string> = {
  AVATAR_VIDEO_UNSUPPORTED_FORMAT: copy.errors.videoType,
  AVATAR_VIDEO_TOO_LARGE: copy.errors.videoTooLarge,
  AVATAR_VIDEO_DURATION_INVALID: copy.errors.videoTooLong,
  AVATAR_VIDEO_RESOLUTION_INVALID: copy.errors.videoResolution,
  AVATAR_VIDEO_CODEC_INVALID: copy.errors.videoCodec,
  AVATAR_VIDEO_AUDIO_CODEC_INVALID: copy.errors.videoAudioCodec,
  AVATAR_VIDEO_DECODE_FAILED: copy.errors.videoUnreadable,
  AVATAR_VIDEO_NOT_READABLE: copy.errors.videoUnreadable,
  AVATAR_VIDEO_ASSET_NOT_FOUND: copy.errors.videoAssetLost
};

/**
 * Map a thrown error to user-facing text: surface the backend ApiError.message
 * (so users see the real reason, e.g. "Active subscription not found."), keep the
 * curated friendly copy for quota, and fall back to generic for a non-ApiError or
 * an empty message. Shared by the workbench forms (数字人口播 + 电商带货 i2v).
 */
export function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "tenant_quota_exceeded") return copy.errors.quota;
    // VIP 门禁（ADMIN-VIP-GATE-UI-0001 §二之二）：doubao 通路无权限 → 友好中文；与「槽位空」(VOICE_CLONE_SLOT_UNAVAILABLE) 区分。
    if (err.code === "VOICE_CLONE_PLAN_REQUIRED") return copy.errors.voiceClonePlanRequired;
    // 出镜视频源校验码 → 友好中文（BE 权威二次校验，含前端读不到的 codec/容器）。
    const videoErr = err.code ? AVATAR_VIDEO_ERROR_COPY[err.code] : undefined;
    if (videoErr) return videoErr;
    // 特定条：商品表批量(seedance_i2v)缺音色 → 后端英文「voice_id is required」映射为中文（通用映射不动）。
    if (/voice_id/i.test(err.message ?? "")) return copy.errors.voiceRequired;
    return err.message || copy.errors.generic;
  }
  return copy.errors.generic;
}
