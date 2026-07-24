import { copy } from "@/lib/copy";

/**
 * Stable backend error_code → friendly Chinese copy (VIDEO-ERR-MAP contract,
 * 照抄图片线 image-error.ts). 与后端 VIDEO-ERR-MAP-BE 共用四码：
 * { VIDEO_INSUFFICIENT_BALANCE, VIDEO_TIMEOUT, VIDEO_CONNECTION_ERROR, VIDEO_GEN_FAILED }。
 * 另含批量视频失败面复用的可操作码 BATCH_IMAGE_DOWNLOAD_FAILED（batch-detail 批量子任务亦是视频任务）。
 */
const VIDEO_ERROR_COPY: Record<string, string> = {
  VIDEO_INSUFFICIENT_BALANCE: copy.errors.videoInsufficientBalance,
  VIDEO_TIMEOUT: copy.errors.videoTimeout,
  VIDEO_CONNECTION_ERROR: copy.errors.videoConnection,
  VIDEO_GEN_FAILED: copy.errors.videoGeneric,
  BATCH_IMAGE_DOWNLOAD_FAILED: copy.errors.batchImageDownloadFailed,
  // V2V-FIX1（#216 真联调）：参考视频内容审核拒（含真人）——**任务执行期异步失败**（非提交 422），走本失败映射。
  // BE workers/video_gen.py:56-58；SPIKE 实测审核拒 credits_cost=0 → 文案说明未扣积分（curated 比 BE message 多这句）。
  VIDEO_REFERENCE_CONTENT_REJECTED: copy.workbench.vgVideoModerationRejected
};

// 技术/裸串特征（英文错误码、JSON 花括号、URL、栈帧等）——用于硬化契约：即便调用方误传裸 error_message 作
// fallback，也不吐出去（回落通用兜底），使「绝不回落裸 error_message」成为函数级硬保证而非仅调用方约定。
const RAW_MARKER = /error code|traceback|exception|\bat\s|https?:\/\/|[{}]|[a-z]+error:/i;

/**
 * Map a video task's backend error_code to friendly, actionable Chinese. Known
 * code → its copy; unknown/missing → a generic friendly line (or the caller's
 * safe fallback). NEVER returns the raw error_message / 技术串：未知码时，若 fallback 形似技术裸串则丢弃，
 * 回落 videoGeneric（硬保证，不靠调用方自觉）。
 */
export function friendlyVideoError(errorCode?: string | null, fallback?: string): string {
  const known = errorCode ? VIDEO_ERROR_COPY[errorCode] : undefined;
  if (known) return known;
  if (fallback && !RAW_MARKER.test(fallback)) return fallback;
  return copy.errors.videoGeneric;
}
