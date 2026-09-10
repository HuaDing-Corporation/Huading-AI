import { copy } from "@/lib/copy";
import { readVideoMetadata, type VideoMetadata } from "@/lib/media/avatar-video";

// 视频反推 source 客户端预检（VIDEO-REVERSE-PROMPT-UI-0001）。后端仍二次把关（BE 权威），这是快速第一道。
// 冻结契约：MP4（H.264）/ ≤200MB / 时长 **1–180s** / **音轨可缺省**（不强制 AAC，故不校验音频）。
// 对齐 uploads.py 的反推像素门：短边≥240、长边≤2160；不沿用数字人出镜视频的 360/1920 边界。
//
// 🔴 D2 放宽 60→180 秒（§八 8.3 / 8.1）：SPIKE 已真跑 180s 走方案 A（6 段 × 30 秒 × 6 帧 = 36 帧 + 1 次汇总，
//    端到端 198.312s、¥1.981348、时间轴 100% 覆盖），故上限从 60 提到 180。
//    本常量是**前端这一侧「1–180 秒」的唯一数值真源**——copy.errors.reverseVideoDuration 与
//    copy.reverse.videoUploadHint 两处文案跟着它改，不许各写各的。
//    ⚠️ 与之相邻但**不同类、不要一起改**：MAX_SCRIPT_SECONDS=60（OmniHuman 口播音频上限，lib/sse/constants.ts:8）。
export const ALLOWED_REVERSE_VIDEO_TYPES = ["video/mp4"];
export const MAX_REVERSE_VIDEO_BYTES = 200 * 1024 * 1024; // 200MB
export const MIN_REVERSE_VIDEO_SEC = 1;
export const MAX_REVERSE_VIDEO_SEC = 180;
export const REVERSE_VIDEO_DURATION_TOLERANCE = 0.5; // 元数据时长抖动容差（BE 权威严格把关）
export const MIN_REVERSE_VIDEO_DIMENSION = 240;
export const MAX_REVERSE_VIDEO_DIMENSION = 2160;

/** MIME + 大小同步预检（不信文件名，按 file.type）。返回友好中文错误，合规返回 null。 */
export function validateReverseVideoFile(file: File): string | null {
  if (!ALLOWED_REVERSE_VIDEO_TYPES.includes(file.type)) return copy.errors.videoType;
  if (file.size > MAX_REVERSE_VIDEO_BYTES) return copy.errors.videoTooLarge;
  return null;
}

/** 时长/像素预检（纯函数）。保持既有时长容差；像素对齐 BE 短边≥240、长边≤2160。 */
export function validateReverseVideoMetadata(meta: VideoMetadata): string | null {
  if (!meta.duration || !Number.isFinite(meta.duration)) return copy.errors.videoUnreadable;
  if (
    meta.duration < MIN_REVERSE_VIDEO_SEC - REVERSE_VIDEO_DURATION_TOLERANCE ||
    meta.duration > MAX_REVERSE_VIDEO_SEC + REVERSE_VIDEO_DURATION_TOLERANCE
  ) {
    return copy.errors.reverseVideoDuration;
  }
  if (!Number.isFinite(meta.width) || !Number.isFinite(meta.height)) return copy.errors.videoUnreadable;
  if (
    Math.min(meta.width, meta.height) < MIN_REVERSE_VIDEO_DIMENSION ||
    Math.max(meta.width, meta.height) > MAX_REVERSE_VIDEO_DIMENSION
  ) {
    return copy.errors.reverseVideoResolution;
  }
  return null;
}

/** 完整预检：MIME/大小 → 元数据(时长/像素)。返回第一个友好错误，全通过返回 null。 */
export async function validateReverseVideo(file: File): Promise<string | null> {
  const fileErr = validateReverseVideoFile(file);
  if (fileErr) return fileErr;
  const meta = await readVideoMetadata(file);
  if (!meta) return copy.errors.videoUnreadable;
  return validateReverseVideoMetadata(meta);
}
