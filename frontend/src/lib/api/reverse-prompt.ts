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

/**
 * BE fill_targets 6 键，内层字段一字不差（backend services/reverse_prompt.py::fill_targets）。
 *
 * REVERSE-DEEP-UI-0001 · 冻结契约 §4.2「破坏性最小」升级：**既有键全部保留、只新增键**，且新增键
 * **一律 optional** —— BE 没给的字段，前端不许拿空串去覆盖用户已填的控件（消费侧纪律见 page.tsx:57）。
 *  - `duration_sec` 由 BE 按目标模块合法区间 clamp（video_gen 4–15 整数 / seedance_i2v 5–120），
 *    并附 `duration_clamped: bool` 供前端显示 D8 那行「已按上限带入」提示（**不许静默改数**）。
 *  - `aspect_ratio` 由 **BE** 按 D7 映射成目标模块各自的合法枚举（图片 8 档+auto / 视频 7 档+auto 两套不同），
 *    映射不出来给 null（不冒充）。前端不猜；各目标表单消费时再按**自己那份枚举常量**兜一道（单一真源在表单侧，
 *    此处不重复一份枚举以免漂移）。
 * ✅ `video_gen.topic`：证伪点①已被 §八 **M3** 采纳（§4.2 抬头「既有键全部保留」与键列表漏写 topic 是文档笔误，
 *    **保留 topic**）。此处维持 optional 声明即可（BE 留着也吃），前端本就不消费它（video_gen 用 prompt 兼作 topic）。
 * ✅ `shot_section`：证伪点②已被 §八 **M2** 采纳 —— 分镜段**不再拼进 `structured_prompt.en`**（那是隐式字符串协议，
 *    逼前端解析段头才能单独取消，脆弱）。改为 BE 单独给一个**拼好的完整分镜段文本（含段头）**，
 *    **前端只拼不拆**（消费处见 prefill-confirm-dialog 的 shots 伪项 + joinShotSection）。
 *    只有 video_gen / seedance_i2v 有此键（§八 M2 只列这两个模块）；photo 没有 → 不造分镜项。
 */
export interface ReversePromptFillTargets {
  avatar_talk: { topic: string; script: string };
  seedance_i2v: {
    topic: string;
    script?: string;
    scene_prompt: string;
    negative_prompt?: string;
    shot_section?: string | null; // §八 M2：完整分镜段（含段头），拼在 scene_prompt 末尾
    duration_sec?: number;
    duration_clamped?: boolean;
  };
  video_gen: {
    topic?: string; // §八 M3 明确保留（现状即有，back-compat）；前端不消费
    prompt: string;
    negative_prompt?: string;
    shot_section?: string | null; // §八 M2：完整分镜段（含段头），拼在 prompt 末尾
    aspect_ratio?: string | null;
    duration_sec?: number;
    duration_clamped?: boolean;
    generate_audio?: boolean;
  };
  photo: {
    topic: string;
    master_prompt?: string;
    negative_prompt?: string;
    aspect_ratio?: string | null;
  };
  ecom_model: { extra_prompt: string; aspect_ratio?: string | null };
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
  audio_transcript?: string | null; // D4 SPIKE 通过才非 null；未通过 → 恒 null，界面如实标注不做假入口
  bgm_style?: string | null; // 同上
  /**
   * REVERSE-DEEP-UI-0001 §4.1：把 shot_list 压成一段**可直接进提示词**的分镜描述文本。
   * optional —— 老结构结果（历史里大量存量）没有这个键，前端必须回落、不许显示 undefined。
   */
  shot_summary?: string | null;
}

/**
 * REVERSE-DEEP-UI-0001 §4.1 新增 —— 原素材的**客观事实**，null 表示测不到（不许冒充）。
 * `aspect_ratio_raw` 仅供展示（例 "9:16" 的约分结果），**不直接进控件**：进控件的是 BE 按 D7 映射好的
 * 目标模块合法枚举值（见 fill_targets.*.aspect_ratio）。
 */
export interface ReverseSourceMedia {
  kind: "image" | "video";
  width: number | null;
  height: number | null;
  duration_sec: number | null; // 图片恒 null
  aspect_ratio_raw: string | null;
}

/**
 * REVERSE-DEEP-UI-0001 §4.1/§4.3 新增 —— 结构化主提示词。
 * `en` 分行标注版（Subject:/Scene:/Composition:/Camera:/Lighting:/Motion:/Style:）供 provider 消费；
 * `zh` 同结构中文版仅供界面展示与用户理解。
 * 🔴 §八 M1/M2 两处修订：① 由 BE service **确定性拼装**（不再问模型要，避免二次摘要丢信息）；
 *    ② **不含分镜段** —— 分镜走独立的 `fill_targets.*.shot_section`，故前端展示时**直接整串渲染、不做任何拆分**。
 * ⚠️ optional：老数据/历史记录没有本字段 → 展示与带入**回落** prompt_zh / prompt_en（必测，不许白屏/undefined）。
 */
export interface ReverseStructuredPrompt {
  en: string;
  zh: string;
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
  // REVERSE-DEEP-UI-0001 §4.1 新增，均 optional（老结构结果没有 → 回落既有字段展示，见 result-view）。
  structured_prompt?: ReverseStructuredPrompt | null;
  source_media?: ReverseSourceMedia | null;
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
  /**
   * §八 M5 分段进度（D10「保持串行、把进度说清楚」的代价转嫁面）：串行推进时 BE 逐段更新。
   * 🔴 **图片路与 ≤60s 短视频路两者恒 null**（BE 不伪造）→ 前端 null 时**什么都不渲染**，
   *    绝不拿 0/undefined 兜出一个假进度（「别做假进度条」）。
   */
  segments_total?: number | null;
  segments_done?: number | null;
}

/**
 * 计费档位（镜像 §八 M4 的 `tier: "image" | "video_short" | "video_long"`）。
 * 🔴 前端**只透传展示，不参与判档**：阈值（60s/180s）与金额（100/250）全在 BE，
 *    此处出现任何阈值常量都是 D9 明令禁止的漂移源。
 */
export type ReverseEstimateTier = "image" | "video_short" | "video_long";

/** `POST /reverse-prompt/estimate` 响应（§八 M4）。duration_sec 图片为 null。 */
export interface ReversePromptEstimate {
  credits: number;
  duration_sec: number | null;
  tier: ReverseEstimateTier;
}

/**
 * 反推计费预估 —— **分档计费的唯一权威**（§八 M4，仿既有 `POST /videos/estimate` 先例）。
 *
 * 🔴 为什么必须走这条：D9 把视频反推改成按时长分档（≤60s / 61–180s 两档，图片另算），而
 *    **档位阈值与积分数一律不许在前端硬编码**。前端自己判档 = 「报价 100、实扣 250」级资金体验事故。
 *    档位由 BE 用**上传时已落库的 duration_ms** 判定，不由客户端传参决定（故请求体只有 source_asset_id）。
 * 🔴 调用失败时调用方**不许猜一个数字兜底**：不显示金额、不允许提交（宁可挡住也不能报错价）。
 *    这与既有 `ConfirmGenerateDialog` 的「暂无法预估，按实际结算」**语义相反**，故不复用那套文案。
 *    差别在于：那边是按实际用量事后结算（估不准无所谓），这边是**一口价预扣**（估错就是扣错）。
 */
export function estimateReversePrompt(input: ReverseFromAssetInput): Promise<ReversePromptEstimate> {
  return apiFetch<ReversePromptEstimate>("/api/v1/reverse-prompt/estimate", { method: "POST", body: input });
}

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

// ── 反推历史（HISTORY-VIDEO-REVERSE-UI-0001）─────────────────────────────────
// 逐字段对齐**已合入 develop 的真 BE**（非冻结文档摘要）：
//   backend/app/schemas/reverse_prompt.py:92-110、routes/reverse_prompt.py:64-87 与 :152-166
//   GET    /api/v1/reverse-prompt/jobs?source_kind=&page=&page_size=  → ReversePromptHistoryListResponse
//   DELETE /api/v1/reverse-prompt/jobs/{id}                           → ReversePromptDeletedResponse（**纯软删**）
// 🔴 列表项**不含 result**（BE schemas:97-103 只有 6 字段）→ 拿不到 fill_targets，「带入生成」必须先取详情。
// 🔴 source_thumbnail_url **仅 image 源有值，video 源恒 null**（services/reverse_prompt.py:320-322）。
// 🔴 DELETE 是软删（只置 deleted_at、不碰媒体/Asset），故不踩 #175 图片删除端点被 revert 的那些雷。

/** 反推来源（镜像 BE ReversePromptSourceKind = Literal["image","video"]，schemas:9）。 */
export type ReverseSourceKind = "image" | "video";

/** 反推任务状态取值集合（BE schema 未用 Literal 锁，权威是 DB CheckConstraint models.py:471）。 */
export type ReverseJobStatus = "queued" | "running" | "succeeded" | "failed" | "saved";

/** 列表项（镜像 BE ReversePromptHistoryItem，schemas:97-103）—— **只有这 6 个字段**。 */
export interface ReversePromptHistoryItem {
  id: string;
  source_kind: ReverseSourceKind;
  status: string; // BE 为裸 str；取值见 ReverseJobStatus（DB 约束），故此处不写死以忠实 BE
  created_at: string; // ISO（BE datetime）
  source_thumbnail_url?: string | null; // 仅 image 有值；video 恒 null
  summary?: string | null; // BE _history_summary：prompt_zh → subject → prompt_en 首个非空，截断 160 字符
}

/** 列表响应（镜像 BE ReversePromptHistoryListResponse，schemas:106-110）。 */
export interface ReversePromptHistoryListResponse {
  items: ReversePromptHistoryItem[];
  total: number;
  page: number;
  page_size: number;
}

/** 软删响应（镜像 BE ReversePromptDeletedResponse，schemas:92-94）。 */
export interface ReversePromptDeletedResponse {
  id: string;
  deleted_at: string;
}

/** BE 列表分页默认 20、上界 100（routes:74-75 ge=1 / ge=1,le=100）。 */
export const REVERSE_PAGE_SIZE = 20;

/**
 * 反推历史列表。source_kind 省略 = 全部（BE routes:70 Optional）；page 从 1 起、page_size ≤100。
 * ⚠️ BE 是 **page/page_size 制**（不是 /videos 历史那套 limit/offset），分页 hook 别抄错。
 */
export function listReversePromptJobs(
  input: { source_kind?: ReverseSourceKind; page?: number; page_size?: number } = {}
): Promise<ReversePromptHistoryListResponse> {
  const sp = new URLSearchParams();
  if (input.source_kind) sp.set("source_kind", input.source_kind); // 省略 → 全部
  sp.set("page", String(input.page ?? 1));
  sp.set("page_size", String(input.page_size ?? REVERSE_PAGE_SIZE));
  return apiFetch<ReversePromptHistoryListResponse>(`/api/v1/reverse-prompt/jobs?${sp.toString()}`, { method: "GET" });
}

/** 软删一条反推历史（BE 只置 deleted_at；列表据此过滤，详情仍可取）。 */
export function deleteReversePromptJob(id: string): Promise<ReversePromptDeletedResponse> {
  return apiFetch<ReversePromptDeletedResponse>(`/api/v1/reverse-prompt/jobs/${encodeURIComponent(id)}`, {
    method: "DELETE"
  });
}

// ── 「带入生成」落点 ──────────────────────────────────────────────────────────
// 工作台一次性 prefill 的富载荷（口播/电商带货 也复用于文案仿写「用此文案」的 script-only 变体）。
// target 与 WorkbenchMode 同名（page.tsx 直接 setMode(target)）：avatar_talk/seedance_i2v/video_gen/photo/ecom_image。
/**
 * REVERSE-DEEP-UI-0001：载荷从「一个提示词」扩到**逐字段直落**（D3-①）。
 * 🔴 每个字段都 optional，且**语义是「本次带入真正带来的字段」**：
 *    key 缺席 = 该控件保持用户当前值不动（目标表单 `if (x !== undefined)` 跳过），**不是清空**。
 *    带入确认弹窗取消勾选某项 = 直接不下发该 key（而非下发空串）——这正是承重门 2。
 * `durationClamped` 是**展示用元数据**（D8 那行提示），目标表单不消费。
 */
export type WorkbenchPrefill =
  | { target: "avatar_talk"; topic?: string; script?: string }
  | {
      target: "seedance_i2v";
      topic?: string;
      scenePrompt?: string;
      script?: string;
      negativePrompt?: string;
      /** §八 M2 分镜段（含段头）。**展示用中间态**：确认弹窗里作为可单独取消的一项，勾选则拼进 scenePrompt。 */
      shotSection?: string;
      durationSec?: number;
      durationClamped?: boolean;
    }
  | {
      target: "video_gen";
      prompt?: string;
      negativePrompt?: string;
      /** 同上，勾选则拼进 prompt。 */
      shotSection?: string;
      aspectRatio?: string;
      durationSec?: number;
      durationClamped?: boolean;
      generateAudio?: boolean;
    }
  | { target: "photo"; prompt?: string; masterPrompt?: string; negativePrompt?: string; aspectRatio?: string }
  | { target: "ecom_image"; tool: "model"; custom?: string; aspectRatio?: string };

/**
 * 勾选分镜表时把 BE 给的分镜段接在主提示词末尾（REVERSE-DEEP-UI-0001-FIX1 · §八 M2）。
 *
 * 🔴 **只拼不拆**。上一版这里还有一个 `splitShotSection`：因为 §4.3 当时把分镜段**拼进** `structured_prompt.en`，
 *    前端要让「分镜表」可单独取消，就只能靠一条正则去认段头（Shots / 分镜表 + 中英文冒号）把它拆回来。
 *    那是**隐式字符串协议** —— BE 改一个字（中文段头、多个空行、换成 "Shot list:"）前端就静默失灵，而且拆出来的
 *    「正文」是否真等于 BE 的原意，代码根本无从校验。§八 M2 已从契约上消灭这个面：分镜段独立成
 *    `fill_targets.*.shot_section`（BE 拼好、**含段头**），拆的那一半随之删除。
 * 🔴 段头由 BE 给 → 前端不再自造 `Shots: ` 前缀，上一版注释里记的「恒发英文段头、BE 若给中文串会中英混排」
 *    这个潜在缺陷**结构性消失**（不是靠测试盯住，是那行代码没有了）。
 * 空段（trim 后为空）→ 原样返回正文，不留一条尾随空行。
 */
export function joinShotSection(body: string, shotSection: string): string {
  const tail = shotSection.trim();
  return tail ? `${body.trimEnd()}\n\n${tail}` : body;
}

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
      // 🔴 只挂 BE **真给了内容**的键：没内容的字段整体不出现在载荷里（展开语法条件挂），
      //    这样目标表单的 `if (x !== undefined)` 才会跳过 → 该控件保持原样。
      // 🔴 **空串按「没给」处理**（Code Review P1）：BE 这套 schema 的惯例是**缺省空串而非省略键**
      //    （见本文件顶部 ReversePromptResult 的 negative_prompt/disclaimer 注释）。若把 "" 当「给了」，
      //    「原素材没有负面提示词」就会变成「把用户已写的负面词清空」——正是承重门2 要防的事，
      //    而且弹窗里只会显示一个空文本框（看着像「没内容可带」，实则会抹掉）。aspect_ratio 本就用真值判据，
      //    此处把同一判据推广到全部可选文本键，去掉这处不一致。
      return t
        ? {
            target: "seedance_i2v",
            topic: t.topic,
            scenePrompt: t.scene_prompt,
            ...(t.script ? { script: t.script } : {}),
            ...(t.negative_prompt ? { negativePrompt: t.negative_prompt } : {}),
            // §八 M2：BE 给 null（图片/无分镜）或空串 → 不挂 → 确认弹窗不产生分镜项（不造死开关）
            ...(t.shot_section ? { shotSection: t.shot_section } : {}),
            ...(t.duration_sec !== undefined ? { durationSec: t.duration_sec } : {}),
            ...(t.duration_clamped !== undefined ? { durationClamped: t.duration_clamped } : {})
          }
        : null;
    }
    case "video_gen": {
      const t = fillTargets.video_gen;
      // aspect_ratio：BE 按 D7 已映射为**视频那套枚举**；映射不出来给 null → 此处不挂（表单侧再按自己的枚举兜一道）。
      return t
        ? {
            target: "video_gen",
            prompt: t.prompt,
            // 空串按「没给」处理（同上 seedance 分支的说明：否则会把用户已填的负面词清空）
            ...(t.negative_prompt ? { negativePrompt: t.negative_prompt } : {}),
            ...(t.shot_section ? { shotSection: t.shot_section } : {}), // §八 M2，同 seedance 分支
            ...(t.aspect_ratio ? { aspectRatio: t.aspect_ratio } : {}),
            ...(t.duration_sec !== undefined ? { durationSec: t.duration_sec } : {}),
            ...(t.duration_clamped !== undefined ? { durationClamped: t.duration_clamped } : {}),
            ...(t.generate_audio !== undefined ? { generateAudio: t.generate_audio } : {})
          }
        : null;
    }
    case "photo": {
      const t = fillTargets.photo;
      // aspect_ratio：BE 按 D7 已映射为**图片那套枚举**（与视频两套不同）；null → 不挂。
      return t
        ? {
            target: "photo",
            prompt: t.topic,
            // 空串按「没给」处理（同上：否则「原图没有总控/负面」会变成「清空用户已填的那两栏」）
            ...(t.master_prompt ? { masterPrompt: t.master_prompt } : {}),
            ...(t.negative_prompt ? { negativePrompt: t.negative_prompt } : {}),
            ...(t.aspect_ratio ? { aspectRatio: t.aspect_ratio } : {})
          }
        : null;
    }
    case "ecom_model": {
      const t = fillTargets.ecom_model;
      return t
        ? {
            target: "ecom_image",
            tool: "model",
            custom: t.extra_prompt,
            ...(t.aspect_ratio ? { aspectRatio: t.aspect_ratio } : {})
          }
        : null;
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
