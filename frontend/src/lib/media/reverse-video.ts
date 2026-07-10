import { copy } from "@/lib/copy";
import { readVideoMetadata, type VideoMetadata } from "@/lib/media/avatar-video";

// 视频反推 source 客户端预检（VIDEO-REVERSE-PROMPT-UI-0001）。后端仍二次把关（BE 权威），这是快速第一道。
// 冻结契约（任务包）：MP4（H.264）/ ≤200MB / 时长 1–60s / **音轨可缺省**（不强制 AAC，故不校验音频）。
// 与数字人出镜视频（3–10s、含分辨率约束）不同——反推只约束容器/大小/时长，分辨率不限。纯函数可测；DOM 读取单列。
export const ALLOWED_REVERSE_VIDEO_TYPES = ["video/mp4"];
export const MAX_REVERSE_VIDEO_BYTES = 200 * 1024 * 1024; // 200MB
export const MIN_REVERSE_VIDEO_SEC = 1;
export const MAX_REVERSE_VIDEO_SEC = 60;
export const REVERSE_VIDEO_DURATION_TOLERANCE = 0.5; // 元数据时长抖动容差（BE 权威严格把关）

/** MIME + 大小同步预检（不信文件名，按 file.type）。返回友好中文错误，合规返回 null。 */
export function validateReverseVideoFile(file: File): string | null {
  if (!ALLOWED_REVERSE_VIDEO_TYPES.includes(file.type)) return copy.errors.videoType;
  if (file.size > MAX_REVERSE_VIDEO_BYTES) return copy.errors.videoTooLarge;
  return null;
}

/** 时长预检（纯函数）。读不到有效元数据→不可读；时长越界(1–60s)→越界错误。分辨率不校验（反推不限）。 */
export function validateReverseVideoMetadata(meta: VideoMetadata): string | null {
  if (!meta.duration || !Number.isFinite(meta.duration)) return copy.errors.videoUnreadable;
  if (
    meta.duration < MIN_REVERSE_VIDEO_SEC - REVERSE_VIDEO_DURATION_TOLERANCE ||
    meta.duration > MAX_REVERSE_VIDEO_SEC + REVERSE_VIDEO_DURATION_TOLERANCE
  ) {
    return copy.errors.reverseVideoDuration;
  }
  return null;
}

/** 完整预检：MIME/大小 → 元数据(时长)。返回第一个友好错误，全通过返回 null。 */
export async function validateReverseVideo(file: File): Promise<string | null> {
  const fileErr = validateReverseVideoFile(file);
  if (fileErr) return fileErr;
  const meta = await readVideoMetadata(file);
  if (!meta) return copy.errors.videoUnreadable;
  return validateReverseVideoMetadata(meta);
}
