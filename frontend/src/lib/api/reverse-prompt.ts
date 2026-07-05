import { apiFetch } from "@/lib/api/client";
import { copy } from "@/lib/copy";

// REVERSE-PROMPT-UI-0001「提示词反推 · 图片」adapter —— 收拢类型 + fetch + 落点映射 + 友好错误，
// 组件只依赖本模块，不散落裸 fetch。契约（任务包 §三，扁平版 + fill_targets）：
//   POST /api/v1/reverse-prompt { source_asset_id, output_language, detail_level } → { jobId, status, result }
//   POST /api/v1/reverse-prompt/{jobId}/regenerate → 同形
//   POST /api/v1/reverse-prompt/{jobId}/save → { saved }
// P1 只图片；只收 source_asset_id（走现有图片上传拿 id）。target_format 固定 seedance_2_0，
// 但类型不写死「只图片」，为 P2 视频留槽。

export type ReversePromptLanguage = "zh" | "en" | "bilingual";
export type ReversePromptDetail = "concise" | "standard" | "expert";

/** BE 已给好的各模块预填载荷——FE 直接 apply，不猜字段。缺某模块即该模块无法「带入」。 */
export interface ReversePromptFillTargets {
  avatar_talk?: { topic: string; script?: string };
  seedance_i2v?: { topic: string; scene_prompt: string };
  video_gen?: { prompt: string; topic: string };
  ecom_image?: { topic: string; extra_prompt: string; poster_title?: string; poster_subtitle?: string };
}

/** 扁平反推结果（任务包 §三）。 */
export interface ReversePromptResult {
  target_format: string; // "seedance_2_0"
  subject: string;
  scene: string;
  composition: string;
  camera: string;
  lighting: string;
  style_tags: string[];
  motion_hint: string;
  prompt_zh: string;
  prompt_en: string;
  negative_prompt: string;
  selling_points?: string[]; // 电商卖点
  text_in_media?: string[]; // 检测到的画面文字（仅识别不执行）
  confidence: number; // 0–1
  disclaimer?: string; // 近似重建红线文案（BE 未给时前端兜底）
  fill_targets: ReversePromptFillTargets;
}

export interface ReversePromptJob {
  jobId: string;
  status: "completed" | "processing" | "failed";
  result?: ReversePromptResult;
  error_code?: string | null;
}

export interface ReverseFromAssetInput {
  source_asset_id: string;
  output_language: ReversePromptLanguage;
  detail_level: ReversePromptDetail;
}

/** 从已上传图片资产反推提示词（P1 同步返回 completed + result）。 */
export function reverseFromAsset(input: ReverseFromAssetInput): Promise<ReversePromptJob> {
  return apiFetch<ReversePromptJob>("/api/v1/reverse-prompt", { method: "POST", body: input });
}

/** 同一 job 重新反推（换一版结果）。 */
export function regenerateReversePrompt(jobId: string): Promise<ReversePromptJob> {
  return apiFetch<ReversePromptJob>(`/api/v1/reverse-prompt/${encodeURIComponent(jobId)}/regenerate`, {
    method: "POST"
  });
}

/** 保存反推结果到历史。 */
export function saveReversePrompt(jobId: string): Promise<{ saved: boolean }> {
  return apiFetch<{ saved: boolean }>(`/api/v1/reverse-prompt/${encodeURIComponent(jobId)}/save`, {
    method: "POST"
  });
}

// ── 「带入生成」落点 ──────────────────────────────────────────────────────────
// 工作台一次性 prefill 的富载荷（口播/电商带货 也复用于文案仿写「用此文案」的 script-only 变体）。
// target 与 WorkbenchMode 同名（page.tsx 直接 setMode(target)）。
export type WorkbenchPrefillTarget = "avatar_talk" | "seedance_i2v" | "video_gen" | "ecom_image";

export type WorkbenchPrefill =
  | { target: "avatar_talk"; topic?: string; script?: string }
  | { target: "seedance_i2v"; topic?: string; scenePrompt?: string; script?: string }
  | { target: "video_gen"; prompt?: string }
  | { target: "ecom_image"; tool: "model" | "poster"; custom?: string; title?: string; tagline?: string };

/**
 * fill_target → WorkbenchPrefill 的落点映射（核心）。BE 载荷直落对应表单字段，缺失即 null（置灰）。
 * 落点：
 *  - avatar_talk  → 数字人口播 topic + script
 *  - seedance_i2v → 电商带货 topic + scene_prompt(→ 画面提示词)
 *  - video_gen    → 视频生成 prompt(同时作 topic)
 *  - ecom_image   → 电商图：有海报标题落「营销海报」(title/subtitle)、否则落「AI 模特」(extra_prompt→自定义补充)
 */
export function fillTargetToPrefill(
  target: WorkbenchPrefillTarget,
  fillTargets: ReversePromptFillTargets
): WorkbenchPrefill | null {
  switch (target) {
    case "avatar_talk": {
      const t = fillTargets.avatar_talk;
      return t ? { target, topic: t.topic, script: t.script } : null;
    }
    case "seedance_i2v": {
      const t = fillTargets.seedance_i2v;
      return t ? { target, topic: t.topic, scenePrompt: t.scene_prompt } : null;
    }
    case "video_gen": {
      const t = fillTargets.video_gen;
      return t ? { target, prompt: t.prompt } : null;
    }
    case "ecom_image": {
      const t = fillTargets.ecom_image;
      if (!t) return null;
      const hasPoster = Boolean(t.poster_title || t.poster_subtitle);
      return {
        target,
        tool: hasPoster ? "poster" : "model",
        custom: t.extra_prompt,
        title: t.poster_title,
        tagline: t.poster_subtitle
      };
    }
    default:
      return null;
  }
}

// ── 友好错误 ─────────────────────────────────────────────────────────────────
// 承 friendlyVideoError 思路：已知码→友好文案；未知/缺失→通用中文兜底；**绝不回落裸 error_message/技术串**。
// 反推暂无稳定 error_code 契约，故 map 留空、只做硬守卫（BE 定码后在此登记，避免伪造契约）。
const RAW_MARKER = /error code|traceback|exception|\bat\s|https?:\/\/|[{}]|[a-z]+error:/i;
const REVERSE_ERROR_COPY: Record<string, string> = {};

export function friendlyReverseError(errorCode?: string | null, fallback?: string): string {
  const known = errorCode ? REVERSE_ERROR_COPY[errorCode] : undefined;
  if (known) return known;
  if (fallback && !RAW_MARKER.test(fallback)) return fallback;
  return copy.errors.reverseFailed;
}
