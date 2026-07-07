import { copy } from "@/lib/copy";

// 数字人「本人出镜视频」客户端预检（AVATAR-VIDEO-SOURCE-UI-0001）。后端仍会二次把关（BE 权威），这是快速
// 第一道。**FE-INTEGRATION-0001 对齐 BE /videos 定稿常量**（backend routes/videos.py _AVATAR_VIDEO_SOURCE_*）：
// MP4 / ≤200MB / 时长 3–10s / 分辨率短边≥360 且长边≤1920（min≥360 && max≤1920）。纯函数可测；DOM 读取单列。

export const ALLOWED_AVATAR_VIDEO_TYPES = ["video/mp4"];
// 200MB：对齐 BE _AVATAR_VIDEO_SOURCE_MAX_BYTES（10s@30Mbps≈37.5MB 的合法视频不得被误拒；FE-INTEGRATION-0001）。
export const MAX_AVATAR_VIDEO_BYTES = 200 * 1024 * 1024;
export const MIN_AVATAR_VIDEO_SEC = 3; // 对齐 BE _AVATAR_VIDEO_SOURCE_MIN_DURATION_MS=3000
export const MAX_AVATAR_VIDEO_SEC = 10; // 对齐 BE _AVATAR_VIDEO_SOURCE_MAX_DURATION_MS=10000
// 元数据时长常有 0.0x 抖动；给 0.5s 容差，避免边界素材被误拒（BE 权威严格把关）。
export const AVATAR_VIDEO_DURATION_TOLERANCE = 0.5;
export const MIN_AVATAR_VIDEO_DIMENSION = 360; // 短边下限（BE _AVATAR_VIDEO_SOURCE_MIN_DIMENSION）
export const MAX_AVATAR_VIDEO_DIMENSION = 1920; // 长边上限（BE _AVATAR_VIDEO_SOURCE_MAX_DIMENSION，非短边）

export interface VideoMetadata {
  duration: number; // 秒
  width: number;
  height: number;
}

/** MIME + 大小同步预检（不信文件名，按 file.type）。返回友好中文错误，合规返回 null。 */
export function validateAvatarVideoFile(file: File): string | null {
  if (!ALLOWED_AVATAR_VIDEO_TYPES.includes(file.type)) return copy.errors.videoType;
  if (file.size > MAX_AVATAR_VIDEO_BYTES) return copy.errors.videoTooLarge;
  return null;
}

/** 时长 + 分辨率预检（纯函数）。读取不到有效元数据→不可读；时长越界(3–10s)/分辨率越界→对应友好错误。 */
export function validateAvatarVideoMetadata(meta: VideoMetadata): string | null {
  if (!meta.duration || !Number.isFinite(meta.duration) || !meta.width || !meta.height) {
    return copy.errors.videoUnreadable;
  }
  // 时长 3–10s（含容差）；短于 3s 与长于 10s 均越界，对齐 BE。
  if (
    meta.duration < MIN_AVATAR_VIDEO_SEC - AVATAR_VIDEO_DURATION_TOLERANCE ||
    meta.duration > MAX_AVATAR_VIDEO_SEC + AVATAR_VIDEO_DURATION_TOLERANCE
  ) {
    return copy.errors.videoTooLong;
  }
  // 对齐 BE：短边≥360 且长边≤1920（非「短边≤1080」——避免误拒 800×2000 之外的合法尺寸）。
  if (
    Math.min(meta.width, meta.height) < MIN_AVATAR_VIDEO_DIMENSION ||
    Math.max(meta.width, meta.height) > MAX_AVATAR_VIDEO_DIMENSION
  ) {
    return copy.errors.videoResolution;
  }
  return null;
}

/** 用隐藏 <video> 读取时长/宽高（loadedmetadata）；读取失败/超时→null。objectURL 用后即回收。 */
export function readVideoMetadata(file: File): Promise<VideoMetadata | null> {
  return new Promise((resolve) => {
    let url: string | null = null;
    try {
      url = URL.createObjectURL(file);
    } catch {
      resolve(null);
      return;
    }
    const video = document.createElement("video");
    video.preload = "metadata";
    let done = false;
    const finish = (result: VideoMetadata | null) => {
      if (done) return;
      done = true;
      if (url) URL.revokeObjectURL(url);
      video.removeAttribute("src");
      video.load();
      resolve(result);
    };
    video.onloadedmetadata = () =>
      finish({ duration: video.duration, width: video.videoWidth, height: video.videoHeight });
    video.onerror = () => finish(null);
    window.setTimeout(() => finish(null), 8000); // 读取超时兜底
    video.src = url;
  });
}

/** 完整预检：MIME/大小 → 元数据(时长/分辨率)。返回第一个友好错误，全通过返回 null。 */
export async function validateAvatarVideo(file: File): Promise<string | null> {
  const fileErr = validateAvatarVideoFile(file);
  if (fileErr) return fileErr;
  const meta = await readVideoMetadata(file);
  if (!meta) return copy.errors.videoUnreadable;
  return validateAvatarVideoMetadata(meta);
}
