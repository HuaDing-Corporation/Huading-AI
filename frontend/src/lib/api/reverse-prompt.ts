import { apiFetch } from "@/lib/api/client";
import { copy } from "@/lib/copy";

// REVERSE-PROMPT-UI「提示词反推 · 图片」adapter —— 收拢类型 + fetch + 落点映射 + 友好错误，组件只依赖本模块。
// FIX1：对齐 BE 真契约（backend/app/schemas/reverse_prompt.py + routes/reverse_prompt.py，已逐字核对）：
//   请求体仅 { source_asset_id }（BE extra="forbid"，多发即 422；target_format 可省、默认 seedance_2_0）
//   POST /api/v1/reverse-prompt                      → ReversePromptJobRead（同步，读 .id + .result）
//   POST /api/v1/reverse-prompt/jobs/{id}/regenerate → ReversePromptJobRead
//   POST /api/v1/reverse-prompt/jobs/{id}/save       → ReversePromptSavedResponse { id, status:"saved", saved_at }
//   成功 status="succeeded"（含 result）；失败经 AppError 抛出（apiFetch throws），非返回 result:null。
// P1 只图片；只收 source_asset_id（走现有图片上传拿 asset_id）。target_format 固定 seedance_2_0，类型未写死留 P2 槽。

/** BE fill_targets 6 键，内层字段一字不差（backend services/reverse_prompt.py::fill_targets）。 */
export interface ReversePromptFillTargets {
  avatar_talk: { topic: string; script: string };
  seedance_i2v: { topic: string; scene_prompt: string };
  video_gen: { topic: string; prompt: string };
  photo: { topic: string };
  ecom_model: { extra_prompt: string };
  ecom_poster: { title: string; subtitle: string };
}

/** BE fill_targets 的 6 个键（「带入」按键即此）。 */
export type ReversePromptFillTargetKey = keyof ReversePromptFillTargets;

/** 扁平反推结果（镜像 BE ReversePromptResult）。negative_prompt/disclaimer 默认空串，数组默认空。 */
export interface ReversePromptResult {
  target_format: string; // "seedance_2_0"
  prompt_zh: string;
  prompt_en: string;
  negative_prompt: string;
  style_tags: string[];
  camera: string;
  lighting: string;
  composition: string;
  subject: string;
  scene: string;
  motion_hint: string;
  selling_points: string[];
  text_in_media: string[];
  disclaimer: string; // 近似重建红线（BE 默认空串 → 前端兜底文案）
  confidence: number; // 0–1
  fill_targets: ReversePromptFillTargets;
}

/** 镜像 BE ReversePromptJobRead（前端主要读 id/status/result/error_*，其余字段照收）。 */
export interface ReversePromptJobRead {
  id: string;
  status: string; // "succeeded" | "running" | "failed" | "saved"
  source_kind: string; // "image"
  source_asset_id?: string | null;
  target_format: string;
  result?: ReversePromptResult | null;
  error_code?: string | null;
  error_message?: string | null;
  provider?: string | null;
  model?: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  credits: number;
  cost_cents: number;
  created_at: string;
  updated_at: string;
  saved_at?: string | null;
}

export interface ReversePromptSavedResponse {
  id: string;
  status: "saved";
  saved_at: string;
}

/** 请求体仅 source_asset_id（BE extra="forbid"）。 */
export interface ReverseFromAssetInput {
  source_asset_id: string;
}

/** 从已上传图片资产反推提示词（同步返回 succeeded + result）。 */
export function reverseFromAsset(input: ReverseFromAssetInput): Promise<ReversePromptJobRead> {
  return apiFetch<ReversePromptJobRead>("/api/v1/reverse-prompt", { method: "POST", body: input });
}

/** 同一 job 重新反推（换一版结果）。 */
export function regenerateReversePrompt(id: string): Promise<ReversePromptJobRead> {
  return apiFetch<ReversePromptJobRead>(`/api/v1/reverse-prompt/jobs/${encodeURIComponent(id)}/regenerate`, {
    method: "POST"
  });
}

/** 保存反推结果到历史。 */
export function saveReversePrompt(id: string): Promise<ReversePromptSavedResponse> {
  return apiFetch<ReversePromptSavedResponse>(`/api/v1/reverse-prompt/jobs/${encodeURIComponent(id)}/save`, {
    method: "POST"
  });
}

// ── 「带入生成」落点 ──────────────────────────────────────────────────────────
// 工作台一次性 prefill 的富载荷（口播/电商带货 也复用于文案仿写「用此文案」的 script-only 变体）。
// target 与 WorkbenchMode 同名（page.tsx 直接 setMode(target)）：avatar_talk/seedance_i2v/video_gen/photo/ecom_image。
export type WorkbenchPrefill =
  | { target: "avatar_talk"; topic?: string; script?: string }
  | { target: "seedance_i2v"; topic?: string; scenePrompt?: string; script?: string }
  | { target: "video_gen"; prompt?: string }
  | { target: "photo"; prompt?: string }
  | { target: "ecom_image"; tool: "model" | "poster"; custom?: string; title?: string; tagline?: string };

/**
 * BE fill_target 键 → WorkbenchPrefill 落点映射（核心）。BE 载荷直落对应表单字段，缺键即 null（置灰）。
 * 落点（6 键 → 5 个工作台模式）：
 *  - avatar_talk  → 数字人口播 new-video-form: topic→topic, script→script
 *  - seedance_i2v → 电商带货 ecom-video-form: topic→topic, scene_prompt→scenePrompt
 *  - video_gen    → 视频生成 video-gen-form: prompt→prompt(同时作 topic)
 *  - photo        → 图片生成 photo-image-form: topic→prompt(提示词即主题)
 *  - ecom_model   → 电商图·AI 模特 (ecom_image tool=model): extra_prompt→自定义补充
 *  - ecom_poster  → 电商图·营销海报 (ecom_image tool=poster): title→标题, subtitle→自定义一行
 */
export function fillTargetToPrefill(
  key: ReversePromptFillTargetKey,
  fillTargets: ReversePromptFillTargets
): WorkbenchPrefill | null {
  switch (key) {
    case "avatar_talk": {
      const t = fillTargets.avatar_talk;
      return t ? { target: "avatar_talk", topic: t.topic, script: t.script } : null;
    }
    case "seedance_i2v": {
      const t = fillTargets.seedance_i2v;
      return t ? { target: "seedance_i2v", topic: t.topic, scenePrompt: t.scene_prompt } : null;
    }
    case "video_gen": {
      const t = fillTargets.video_gen;
      return t ? { target: "video_gen", prompt: t.prompt } : null;
    }
    case "photo": {
      const t = fillTargets.photo;
      return t ? { target: "photo", prompt: t.topic } : null;
    }
    case "ecom_model": {
      const t = fillTargets.ecom_model;
      return t ? { target: "ecom_image", tool: "model", custom: t.extra_prompt } : null;
    }
    case "ecom_poster": {
      const t = fillTargets.ecom_poster;
      return t ? { target: "ecom_image", tool: "poster", title: t.title, tagline: t.subtitle } : null;
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

export function friendlyReverseError(errorCode?: string | null, fallback?: string | null): string {
  const known = errorCode ? REVERSE_ERROR_COPY[errorCode] : undefined;
  if (known) return known;
  if (fallback && !RAW_MARKER.test(fallback)) return fallback;
  return copy.errors.reverseFailed;
}
