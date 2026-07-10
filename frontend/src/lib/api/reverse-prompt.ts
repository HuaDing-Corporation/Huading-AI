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

/**
 * 视频反推分析（VIDEO-REVERSE-PROMPT-UI-0001 · FIX2 逐字段对齐已合入的真 BE
 * backend/app/schemas/reverse_prompt.py::ReversePromptVideoAnalysis / ReversePromptShot）。展示：时长 / 节奏 /
 * 分镜列表 shot_list；audio_transcript / bgm_style 可空（空→前端标「未启用」）。
 */
/** 节奏枚举（BE ReversePromptVideoAnalysis.pacing = Literal["slow","medium","fast","variable"]，非中文串）。 */
export type ReverseVideoPacing = "slow" | "medium" | "fast" | "variable";

/** 分镜（镜像 BE ReversePromptShot）：index 必填(≥0)；start_sec(≥0)/end_sec(>0)；camera/motion/transition BE 默认 ""。 */
export interface ReverseVideoShot {
  index: number; // 分镜序号（必填，≥0）—— FIX2 加回
  start_sec: number; // 起始秒（≥0）
  end_sec: number; // 结束秒（>0）
  visual: string; // 画面描述
  camera: string; // 运镜（BE 默认 ""）
  motion: string; // 主体动作（BE 默认 ""）
  transition: string; // 转场（BE 默认 ""）
}
export interface ReverseVideoAnalysis {
  duration_sec: number; // 时长秒（>0）
  pacing: ReverseVideoPacing; // 节奏枚举（前端映射中文显示，不显裸英文）
  shot_list: ReverseVideoShot[];
  audio_transcript?: string | null; // 一期空
  bgm_style?: string | null; // 一期空
}

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
  // FIX1：视频源反推的 video_analysis **内嵌于 result**（job.result.video_analysis），非 job 顶层；图片源为空。
  video_analysis?: ReverseVideoAnalysis | null;
}

/** 镜像 BE ReversePromptJobRead（前端主要读 id/status/result/error_*，其余字段照收）。 */
export interface ReversePromptJobRead {
  id: string;
  status: string; // "succeeded" | "running" | "processing" | "failed" | "saved"
  source_kind: string; // "image" | "video"
  source_asset_id?: string | null;
  target_format: string;
  result?: ReversePromptResult | null; // FIX1：视频源的 video_analysis 内嵌于此（result.video_analysis）
  error_code?: string | null;
  error_message?: string | null;
  provider?: string | null;
  model?: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  credits: number; // FIX1：**provider credits**（引擎调用成本），非租户扣费；租户固定 100 积分由 BE UsageRecord 记
  cost_cents: number;
  created_at: string;
  updated_at: string;
  saved_at?: string | null;
}

/**
 * 视频反推·租户计费：固定 100 积分/次（确认弹窗展示；扣费在后端 submit 时以 UsageRecord 记，与 job.credits 无关）。
 * 注意区分：本常量=租户扣费口径；job.credits=provider 引擎成本，二者语义不同，界面计费门只用本常量。
 */
export const REVERSE_VIDEO_CREDITS = 100;

/** 反推任务是否终态（用于视频异步轮询判定；图片同步不经此路径）。 */
export function isReverseSettled(status: string): boolean {
  return status === "succeeded" || status === "failed";
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

/**
 * 从已上传资产反推提示词。请求体仅 source_asset_id，**后端据资产推 source_kind**：
 *  - 图片 → 同步返回 status="succeeded" + result；
 *  - 视频 → 异步 202 返回 status="running"（无 result），前端轮询 getReversePromptJob 到终态。
 */
export function reverseFromAsset(input: ReverseFromAssetInput): Promise<ReversePromptJobRead> {
  return apiFetch<ReversePromptJobRead>("/api/v1/reverse-prompt", { method: "POST", body: input });
}

/** 轮询反推任务（视频异步用）。GET /reverse-prompt/jobs/{id}。 */
export function getReversePromptJob(id: string): Promise<ReversePromptJobRead> {
  return apiFetch<ReversePromptJobRead>(`/api/v1/reverse-prompt/jobs/${encodeURIComponent(id)}`, { method: "GET" });
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
  | { target: "ecom_image"; tool: "model"; custom?: string };

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
    case "ecom_poster":
      // 营销海报已下线（ECOM-REPLICATE-UI-0001）→ 无落点，返 null（「带入·营销海报」按钮已移除）。
      return null;
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
