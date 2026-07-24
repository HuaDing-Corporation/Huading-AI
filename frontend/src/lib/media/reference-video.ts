import { isReadableVideoMetadata, readVideoMetadata, type VideoMetadata } from "@/lib/media/avatar-video";
import { copy } from "@/lib/copy";

// 视频生成·参考视频 客户端预检（VIDEO-GEN-V2V-UI-0001）。契约=冻结文档 §SPIKE + D8–D10（provider 硬限制）：
// 格式 JPG/PNG/WEBP 之外视频收 MP4/MOV/WEBM、≤100MB（D9 表）、最多 3 条（D10）、**合计时长 1.8–15.2s**（D10 联动）、
// 参考分辨率 480P–720P——D9：>720p 服务端自动降码（UI 告知「已自动压缩」）、MOV/WEBM 服务端自动转 MP4（告知）、
// **单条 >15.2s 拒绝**（内容决策不代剪）、<480p 拒绝、>100MB 拒绝。BE 权威二次把关；此为快速第一道 + 联动提示数据源。
// 复用 avatar-video 的 readVideoMetadata（隐藏 <video> loadedmetadata）；纯函数可测，DOM 读取单列。

export const ALLOWED_REFERENCE_VIDEO_TYPES = ["video/mp4", "video/quicktime", "video/webm"]; // MOV=video/quicktime
export const MAX_REFERENCE_VIDEO_BYTES = 100 * 1024 * 1024; // 100MB（本需求 UI 定值，非 avatar 的 200MB）
export const MAX_REFERENCE_VIDEOS = 3; // provider 上限（D10）
export const MIN_TOTAL_REFERENCE_SEC = 1.8; // 合计时长下限（provider 硬限）
export const MAX_TOTAL_REFERENCE_SEC = 15.2; // 合计时长上限（provider 硬限；单条超此值必然超合计 → 拒）
// 元数据时长常有 0.0x 抖动；单条上限给 0.2s 容差（保守小于 avatar 的 0.5：15.2 是 provider 硬墙，BE/provider 严格把关）。
export const REFERENCE_VIDEO_DURATION_TOLERANCE = 0.2;
export const MIN_REFERENCE_VIDEO_DIMENSION = 480; // 短边 <480p → 拒（D9）
export const MAX_REFERENCE_VIDEO_DIMENSION = 720; // 短边 >720p → 不拒，标记「将自动压缩」（D9 服务端降码）

/** 预检结论：拒绝（error 非空）或通过（含元数据 + 转码/压缩告知标记）。 */
export interface ReferenceVideoInspection {
  error: string | null;
  meta: VideoMetadata | null;
  /** MOV/WEBM → 服务端自动转 MP4（D9，告知不静默）。 */
  willTranscode: boolean;
  /** 短边 >720p → 服务端自动降码（D9，告知「已自动压缩」）。 */
  willDownscale: boolean;
}

const rejected = (error: string): ReferenceVideoInspection => ({ error, meta: null, willTranscode: false, willDownscale: false });

/** MIME + 大小同步预检（不信文件名，按 file.type）。 */
export function validateReferenceVideoFile(file: File): string | null {
  if (!ALLOWED_REFERENCE_VIDEO_TYPES.includes(file.type)) return copy.workbench.vgRefVideoType;
  if (file.size > MAX_REFERENCE_VIDEO_BYTES) return copy.workbench.vgRefVideoTooLarge;
  return null;
}

/** 元数据预检（纯函数）：单条 >15.2s 拒（D9 不代剪）；短边 <480p 拒；短边 >720p 通过但标记降码告知。 */
export function inspectReferenceVideoMetadata(file: File, meta: VideoMetadata): ReferenceVideoInspection {
  if (!isReadableVideoMetadata(meta)) {
    return rejected(copy.errors.videoUnreadable);
  }
  if (meta.duration > MAX_TOTAL_REFERENCE_SEC + REFERENCE_VIDEO_DURATION_TOLERANCE) {
    return rejected(copy.workbench.vgRefVideoTooLong);
  }
  const shortSide = Math.min(meta.width, meta.height);
  if (shortSide < MIN_REFERENCE_VIDEO_DIMENSION) return rejected(copy.workbench.vgRefVideoResolutionLow);
  return {
    error: null,
    meta,
    willTranscode: file.type !== "video/mp4",
    willDownscale: shortSide > MAX_REFERENCE_VIDEO_DIMENSION
  };
}

/** 完整预检：MIME/大小 → 读元数据 → 元数据规则。组件默认注入；测试可注入受控版（jsdom 无法解码视频）。 */
export async function inspectReferenceVideo(file: File): Promise<ReferenceVideoInspection> {
  const fileErr = validateReferenceVideoFile(file);
  if (fileErr) return rejected(fileErr);
  const meta = await readVideoMetadata(file);
  if (!meta) return rejected(copy.errors.videoUnreadable);
  return inspectReferenceVideoMetadata(file, meta);
}

/** 合计时长状态（D10 联动）：ok=可提交；low=不足 1.8s（提示补）；over=超 15.2s（阻断）。空列表视为 ok（无视频=可选）。 */
export function totalDurationStatus(totalSec: number, count: number): "ok" | "low" | "over" {
  if (count === 0) return "ok";
  if (totalSec > MAX_TOTAL_REFERENCE_SEC) return "over";
  if (totalSec < MIN_TOTAL_REFERENCE_SEC) return "low";
  return "ok";
}
