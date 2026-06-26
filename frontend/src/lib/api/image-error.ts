import { copy } from "@/lib/copy";

/**
 * Stable backend error_code → friendly Chinese copy (IMAGE-ERROR-FRIENDLY
 * contract). Keep keys in sync with the backend classifier
 * (classify_image_error): { IMAGE_MODERATION_BLOCKED, IMAGE_CONNECTION_ERROR,
 * IMAGE_INVALID_REQUEST, IMAGE_GEN_FAILED, IMAGE_ALPHA_MISSING }.
 */
const IMAGE_ERROR_COPY: Record<string, string> = {
  IMAGE_MODERATION_BLOCKED: copy.errors.imageModeration,
  IMAGE_CONNECTION_ERROR: copy.errors.imageConnection,
  IMAGE_INVALID_REQUEST: copy.errors.imageInvalid,
  IMAGE_GEN_FAILED: copy.errors.imageGeneric,
  // 抠图透明底未返回 alpha 像素 → 专属可操作文案（改白底/重试），不落通用兜底
  IMAGE_ALPHA_MISSING: copy.errors.imageAlphaMissing
};

/**
 * Map a photo task's backend error_code to friendly, actionable Chinese. Known
 * code → its copy; unknown/missing → a generic friendly line (or the caller's
 * safe fallback). NEVER returns the raw error_message / OpenAI JSON — callers must
 * not pass raw backend text as the fallback (so the UI can't leak "Error code: 400…").
 */
export function friendlyImageError(errorCode?: string | null, fallback?: string): string {
  const known = errorCode ? IMAGE_ERROR_COPY[errorCode] : undefined;
  return known ?? fallback ?? copy.errors.imageGeneric;
}
