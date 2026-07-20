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

// 图片服务能力码（IMAGE-GEN-OPTIMIZE-UI-0001-FIX1 · BE providers/base.py fail-closed）：租户 provider（如 OpenAI 仅 1K/
// 1 张参考图）不支持所选清晰度或参考图数量时，BE 在建任务/预留积分之前 422，且带**动态友好中文** user_message
// （含“请选择 1K”/“最多支持 N 张”，比任何前端静态文案更精确）。故这里**优先透出 BE message**，仅当其意外为空才落兜底
// curated 文案——绝不落通用「操作失败」。逐字对齐 backend/app/providers/base.py 的 code。
const IMAGE_PROVIDER_CAPABILITY_CODES = new Set([
  "IMAGE_PROVIDER_RESOLUTION_UNSUPPORTED",
  "IMAGE_PROVIDER_REFERENCE_IMAGES_UNSUPPORTED",
  "IMAGE_PROVIDER_CAPABILITIES_UNDECLARED"
]);

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
    // 图片服务能力 422（IMAGE_PROVIDER_* · FIX1）：优先透 BE 动态友好中文，空则兜底——不落通用报错。
    if (err.code && IMAGE_PROVIDER_CAPABILITY_CODES.has(err.code)) return err.message || copy.errors.imageProviderCapability;
    // 视频生成 2000 墙（VIDEO-GEN-PARAMS-UI-0001-FIX1 · #213 真联调）：BE friendly_video_gen_prompt_too_long →
    // code=VIDEO_GEN_PROMPT_TOO_LONG，message=「提示词输入最大上限为 2000 字」（与前端红字一字不差）。前端拦为主，
    // 此为 BE 权威分流兜底（如码点边界/绕过 UI 直调）——透 BE message，空则用同句红字文案，不落通用报错。
    if (err.code === "VIDEO_GEN_PROMPT_TOO_LONG") return err.message || copy.workbench.vgPromptOverLimit;
    // 特定条：商品表批量(seedance_i2v)缺音色 → 后端英文「voice_id is required」映射为中文（通用映射不动）。
    if (/voice_id/i.test(err.message ?? "")) return copy.errors.voiceRequired;
    return err.message || copy.errors.generic;
  }
  return copy.errors.generic;
}
