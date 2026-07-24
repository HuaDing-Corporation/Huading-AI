import { isReadableVideoMetadata, readVideoMetadata, type VideoMetadata } from "@/lib/media/avatar-video";
import { copy } from "@/lib/copy";

// 视频生成·参考视频 客户端预检（VIDEO-GEN-V2V-UI-0001）。契约=冻结文档 §SPIKE + D8–D10（provider 硬限制）：
// 格式 JPG/PNG/WEBP 之外视频收 MP4/MOV/WEBM、≤100MB（D9 表）、最多 3 条（D10）、**合计时长 1.8–15.2s**（D10 联动）、
// 参考分辨率 480P–720P——D9：>720p 服务端自动降码（UI 告知「已自动压缩」）、MOV/WEBM 服务端自动转 MP4（告知）、
// **单条 >15.2s 拒绝**（内容决策不代剪）、<480p 拒绝、>100MB 拒绝。BE 权威二次把关；此为快速第一道 + 联动提示数据源。
// 复用 avatar-video 的 readVideoMetadata（隐藏 <video> loadedmetadata）；纯函数可测，DOM 读取单列。

export const ALLOWED_REFERENCE_VIDEO_TYPES = ["video/mp4", "video/quicktime", "video/webm"]; // MOV=video/quicktime
export const MAX_REFERENCE_VIDEO_BYTES = 100 * 1024 * 1024; // 100MB（本需求 UI 定值，非 avatar 的 200MB）
export const MAX_REFERENCE_VIDEOS = 3; // provider 上限（D10；BE schemas/videos.py:365 >3 → 422）
// FIX1 真联调（#216 合并源）：BE 两套边界**方向必须一致**（别「前端放过、BE 拦」）：
//  · 单条 = **闭区间 [1800,15200]ms 且有下限**（services/video_reference.py:128-137 `< MIN or > MAX` → 422）
//    ——原前端只拦单条超长、无下限、上限还给 0.2s 容差 → 1.0s / 15.3s 都会「前端放过、BE 422」，已改硬边界无容差；
//  · 合计 = **开区间 (1800,15200)ms**（routes/videos.py:936-943 `MIN < total < MAX`，恰 1.8/15.2 也 422）。
export const MIN_REFERENCE_VIDEO_SEC = 1.8; // 单条下限（BE VIDEO_REFERENCE_MIN_DURATION_MS=1_800）
export const MAX_REFERENCE_VIDEO_SEC = 15.2; // 单条上限（BE VIDEO_REFERENCE_MAX_DURATION_MS=15_200）
export const MIN_TOTAL_REFERENCE_SEC = 1.8; // 合计下限（开区间：恰 1.8 不合法）
export const MAX_TOTAL_REFERENCE_SEC = 15.2; // 合计上限（开区间：恰 15.2 不合法）
export const MIN_REFERENCE_VIDEO_DIMENSION = 480; // 短边 <480p → 拒（D9；BE video_reference.py:140-145）
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

/** 元数据预检（纯函数）：单条须在 [1.8,15.2]s（BE 闭区间，过短/过长均拒、D9 不代剪）；短边 <480p 拒；>720p 通过但标记降码告知。 */
export function inspectReferenceVideoMetadata(file: File, meta: VideoMetadata): ReferenceVideoInspection {
  if (!isReadableVideoMetadata(meta)) {
    return rejected(copy.errors.videoUnreadable);
  }
  if (meta.duration < MIN_REFERENCE_VIDEO_SEC) return rejected(copy.workbench.vgRefVideoTooShort);
  if (meta.duration > MAX_REFERENCE_VIDEO_SEC) return rejected(copy.workbench.vgRefVideoTooLong);
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

/** 合计时长状态（D10 联动）：ok=可提交；low/over=阻断。空列表=ok（无视频=可选）。
 * FIX1：**开区间**对齐 BE `MIN < total < MAX`（routes/videos.py:936-943）——恰 1.8/15.2 也不合法（BE 用整数 ms 相加，
 * 前端秒浮点 >= 判恰值为 over/low 与 BE 方向一致、只会更严不更松）。 */
export function totalDurationStatus(totalSec: number, count: number): "ok" | "low" | "over" {
  if (count === 0) return "ok";
  if (totalSec >= MAX_TOTAL_REFERENCE_SEC) return "over";
  if (totalSec <= MIN_TOTAL_REFERENCE_SEC) return "low";
  return "ok";
}
