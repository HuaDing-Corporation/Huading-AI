// Backend contract types (mirror app/schemas/*). Keep in sync with the API.

export interface ApiResponse<T> {
  data: T | null;
  // `outcome`：BE `ErrorEnvelope` 的可选字段，由服务端异常处理挂上（文案三端点见
  // `core/exceptions.py:_copy_generation_error_outcome`）。前端据「有没有它」判断请求是否到达应用层
  // —— 见 `client.ts ApiError.outcome` 与 `copy-billing.ts`。
  error: { code: string; message: string; request_id?: string; detail?: unknown; details?: unknown; outcome?: unknown } | null;
  request_id: string | null;
}

export type Role = "admin" | "ops" | "creator" | "reviewer" | "developer";

export interface TokenResponse {
  access_token: string;
  token_type: string;
  tenant_id: string;
  user_id: string;
  role: Role;
}

export interface TenantRead {
  id: string;
  slug: string;
  name: string;
}

export interface UserRead {
  id: string;
  tenant_id: string;
  email: string;
  full_name: string | null;
  role: Role;
}

export interface CurrentUserResponse {
  tenant: TenantRead;
  user: UserRead;
  permissions: string[];
}

// 注册（AUTH-UI-0001）——镜像 BE TenantRegisterResponse（POST /auth/register-tenant，201）。
export interface TenantRegisterResponse {
  tenant: TenantRead;
  user: UserRead;
  token: TokenResponse;
}

// 后端 VideoTask.status 枚举含 cancelled（批量生产 cancel 退分产生）——db/models.py CHECK 5 档。
// 前端此前只列 4 档，致 fromVideoRead 裸透传的 cancelled 在 thumbIcon 查不到 → #130（ECOM-HISTORY-CANCELLED-FIX-0001）。
export type VideoStatus = "queued" | "running" | "done" | "failed" | "cancelled";

/**
 * 列表项 —— 🔴 **BE 列表返回的是完整 `VideoRead`**，不是精简版：
 * `VideoListResponse.items: list[VideoRead]`（backend/app/schemas/videos.py:345-346），且列表路由用的是
 * **与详情同一个 builder**（`items = [_video_read(task, ...)]`，routes/videos.py:606-609）。
 *
 * 本接口按「FE 实际消费的字段」声明其子集 —— 子集本身没问题，**少声明了在读的字段才是问题**：
 * `fromVideoRead` 曾经要 `read as Partial<VideoDetail>` 才能读 playback_url / download_url /
 * duration_ms / error_message，等于**用强转绕开类型检查**。强转一旦存在，fixture 写错字段名
 * （如 `duration_sec` ← BE 其实给 `duration_ms`）类型层一声不吭，只能等确定值断言在运行时抓 ——
 * 那次就是这么抓到的。故这四个字段补齐、强转删除（HISTORY-VIDEO-DIALOG-UI-0001 · FIX1）。
 */
export interface VideoListItem {
  id: string;
  status: VideoStatus;
  progress: number; // 0..100
  // BE `VideoRead.topic: str | None`（schemas/videos.py:321）→ 可为 null；VideoDetail 那边一直诚实声明着
  // `string | null`，同一个字段两个类型两种说法。消费方 fromVideoRead 本就 `read.topic || "未命名视频"` 兜着。
  topic: string | null;
  // 🔴 HISTORY-FULL-PROMPT-UI-0001：数字人口播用户填的是 script（要念的文案）—— BE 一直在发
  // （_video_read:559 `script=task.script`，列表与详情同一个 builder），只是 FE 没声明。同 #185 补齐
  // playback_url 那批：**少声明了在发也在用的字段**才是问题，子集本身不是。
  script?: string | null;
  mode?: string | null; // avatar_talk | seedance_i2v | photo —结果渲染：视频 vs 图
  kind?: string | null; // 图片细分：如 "cover"（封面 photo task；HIST kind 筛真后端支持）
  error_code?: string | null; // 图片失败时映射友好文案（friendlyImageError）
  error_message?: string | null;
  thumbnail_url?: string | null;
  playback_url?: string | null;
  download_url?: string | null;
  // ⚠️ BE 同时有 duration_sec 和 duration_ms 两个字段（schemas/videos.py:335-336）；FE 消费的是 **ms**
  // （fromVideoRead 除以 1000）。写成 duration_sec 会静默映射不到 —— 这正是补齐类型要拦的那类错。
  duration_ms?: number | null;
  apply_visible_label?: boolean; // 该任务是否带 AI 显式标识（LABEL-TOGGLE-UI-0001，徽标数据源）
  created_at: string;
}
export interface VideoListResponse {
  items: VideoListItem[];
  total: number;
}

export interface VideoDetail {
  id: string;
  status: VideoStatus;
  progress: number;
  mode?: string | null; // avatar_talk | seedance_i2v | photo（photo → 渲染 <img>）
  // Backend VideoRead leaves these nullable (e.g. before the script step runs);
  // align the types so consumers null-handle rather than assume present (P2-3).
  topic: string | null;
  script: string | null;
  voice_id: string | null;
  aspect_ratio: string | null;
  subtitle_enabled: boolean | null;
  apply_visible_label?: boolean; // 该任务是否带 AI 显式标识（LABEL-TOGGLE-UI-0001，详情徽标数据源）
  playback_url?: string | null;
  download_url?: string | null;
  thumbnail_url?: string | null;
  duration_ms?: number | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  finished_at?: string | null;
}

export interface CreateVideoRequest {
  topic?: string; // 数字人口播/照片/视频生成沿用（≤500）；电商带货 i2v 起可选（ECOM-VIDEO-OPTIMIZE-UI-0001 契约 §4.3/req1：空则不带）

  script?: string; // 可选；缺则后端 DeepSeek 生成（前端流程会带）
  voice_id?: string; // 数字人口播 / 电商带货必填；照片 photo 不传（无配音）
  avatar_asset_id?: string; // 数字人口播·照片形象（上传/预设产出的 asset_id）；与 avatar_video_asset_id 互斥
  avatar_video_asset_id?: string; // 数字人口播·本人出镜视频源（AVATAR-VIDEO-SOURCE-UI-0001，与 avatar_asset_id 互斥）；字段形状以 BE 包为准
  video_mode?: string; // 省略=数字人口播 avatar_talk；电商带货传 "seedance_i2v"；视频生成传 "video_gen"
  image_key?: string; // @deprecated 电商带货 i2v 旧单图字段（ECOM-VIDEO-OPTIMIZE-UI-0001 起改用 product_image_keys）；照片 photo 可选参考图仍用，来自 POST /uploads
  product_image_keys?: string[]; // 电商带货 i2v 产品图 1–N（ECOM-VIDEO-OPTIMIZE-UI-0001 契约 §4.3；来自 POST /uploads→key）；下限=至少 1 张（决策2 保底）
  scene_prompt?: string; // 电商带货 i2v 画面提示词（与口播解耦，可 AI 生成）；空则后端回退 topic；已去 max_length（契约 §4.3）
  negative_prompt?: string; // 电商带货 i2v 负面提示词（可选、无限，ECOM-VIDEO-OPTIMIZE-UI-0001 契约 §4.3/req6）；转发 APIMart video body（step0 验 Seedance 吃）
  duration_sec?: number; // 电商带货 i2v 目标时长（秒，5–120，默认 30）；视频生成 4–15 整数（预设 5/10/15 + 自定义，VIDEO-GEN-PARAMS-UI-0001）
  image_size?: string; // @deprecated 照片旧「尺寸」（IMAGE-ASPECT-RATIO-UI-0001 起改用 aspect_ratio；BE 仅在 aspect_ratio 省略时回退翻译）；cover 通路仍可带
  image_quality?: string; // @deprecated 照片旧「质量」（IMAGE-ASPECT-RATIO-UI-0001 起去除；BE 已忽略）；cover 通路仍可带、不影响
  speed?: number; // 默认 1.0
  aspect_ratio?: string; // 画面比例：照片 8 定比+auto 默认 1:1；口播/电商带货视频 9:16；**视频生成(video_gen) 7 值默认 auto**（见下方 video_gen 段注释）
  subtitle_enabled?: boolean; // 默认 true
  subtitle_style?: SubtitleStyle; // 数字人口播：字幕样式覆盖（ORAL-PROD-UI-0001）；缺省=与 0001 默认烧入一致（不回归）
  apply_visible_label?: boolean; // AI 生成显式标识开关（LABEL-TOGGLE-UI-0001）；默认关(false)，开=true。对齐后端 VideoGenerateRequest.apply_visible_label
  purpose?: string; // 照片/封面：用途标识，如 "cover"（AI 封面复用 0003 文生图标识；进图片历史作为 photo）
  // ── 图片生成/修改 photo 优化 (IMAGE-GEN-OPTIMIZE-UI-0001 契约 §四) ──
  // 四层提示词长度（FIX1 真联调订正 · 以合并后 BE schemas/videos.py 为准）：photo 的 topic/master_prompt/
  // master_negative_prompt/negative_prompt 各有 **≤20000 字符**反滥用上界（BE schema 校验，超限→422，
  // 非静默截断）。前端仍不设 maxLength（正常使用远不及；此为诚实注释，非“无上限”）。negative_prompt 仅 photo 分支受此上界，视频语义不限。
  image_keys?: string[]; // 参考图 1–6（取代标量 image_key；来自 POST /uploads→key，格式须 uploads/<name>.{jpg,jpeg,png,webp}）。可选（纯文生图不带）；image_key 保留兼容 AI 封面等既有 caller
  master_prompt?: string; // 任务总控提示词（全局风格前缀，可选，≤20000）
  master_negative_prompt?: string; // 任务统一负面提示词（可选，≤20000）——同 negative_prompt 编码进 prompt，非硬约束
  // 三个强度：均 int|None，取值 10..100 步长 10，None=未开启（默认）。底层编码进提示词（provider 无原生参数），软性倾向、非精确控制。
  // 注：背景参考强度(background_strength)已于 2026-07-19 砍除——BE 盲评判定无作用、#208 内删除、从未上线。
  similarity_strength?: number; // 图片相似度
  creativity_strength?: number; // AI 创意程度
  subject_strength?: number; // 主体保持强度
  image_resolution?: string; // 清晰度档位 "1k"|"2k"|"4k"（§3之二，默认 1k）；界面选择是硬条件、总随请求传（BE 保证参数来源唯一）
  // 注：图片负面提示词复用上方 negative_prompt 字段（视频链路已有；photo 分支此前不读，本期起读）。
  // ── 视频生成 video_gen (VIDEOGEN-UI-0001, seam §2) ──
  prompt?: string; // 提示词（seam 字段，最大 2000 字）；提交时同时复用作 topic，非-photo topic 超 2000 → BE 422（走口播/电商/数字人共用的那道 2000 墙）
  // #214 注释债订正：BE 允许 **0–9**（schemas/videos.py:355，0 张=纯文生视频合法）；V2V-UI-0001 起 UI 参考图也已放开为可选（「参考图或视频（可选）」）。
  reference_image_asset_ids?: string[]; // 参考图 0–9 张（POST /uploads/images → asset_id）；与 reference_video_asset_ids 严格二选一（D8）
  // 视频生视频（VIDEO-GEN-V2V-UI-0001 · FIX1 已逐字对齐 #216 合并源）：参考视频 ≤3 条且唯一（schemas/videos.py:365-374）、
  // **合计时长开区间 (1.8,15.2)s**（routes/videos.py:936-943，BE 整数 ms 相加、恰 1.8/15.2 也 422）、单条闭区间
  // [1.8,15.2]s + 短边 ≥480（services/video_reference.py:128-145）、不得含真人（执行期审核拒 VIDEO_REFERENCE_CONTENT_REJECTED
  // 且不扣积分）；与 reference_image_asset_ids 互斥（VIDEO_GEN_REFERENCE_MEDIA_CONFLICT）。经
  // POST /uploads/videos?purpose=video_gen_reference（uploads.py:240）→ asset_id。
  reference_video_asset_ids?: string[];
  resolution?: string; // 视频分辨率 "480p" | "720p" | "1080p"（默认 720p）
  bgm?: VideoGenBgm; // 可选背景音乐：上传(asset_id) 或 配乐库(track_id)——生成后混音，与 generate_audio 不同
  // VIDEO-GEN-PARAMS-UI-0001（需求4）：音频生成开关，默认 false（零回归）。true=视频模型生成环境音/配乐（SPIKE 实测真出 AAC、同价）。BE 一行改传（provider 现硬编码 False）
  generate_audio?: boolean;
  // aspect_ratio（需求3）复用上方 aspect_ratio 字段：视频侧 7 值 16:9/9:16/1:1/4:3/3:4/21:9/auto（默认 auto；FIX1 真联调：BE API 值=auto，worker 翻译成 provider 的 adaptive）
}

// 视频生成 BGM（seam §2/§3）：上传(复用 /uploads/audio→asset_id) 或 配乐库(track_id) 二选一。
export type VideoGenBgm =
  | { source: "upload"; asset_id: string }
  | { source: "library"; track_id: string };
export type VideoGenResolution = "480p" | "720p" | "1080p";
// 预设档保留 5/10/15；VIDEO-GEN-PARAMS-UI-0001（需求5）新增自定义整数 4–15（provider apimart.py:209 硬钳 max(4,min(15,..))）。
export const VIDEO_GEN_DURATIONS = [5, 10, 15] as const;
export const VIDEO_GEN_DURATION_MIN = 4;
export const VIDEO_GEN_DURATION_MAX = 15;
export type VideoGenDuration = (typeof VIDEO_GEN_DURATIONS)[number];

// GET /bgm-library（seam §3）：平台预置免版权配乐，preview_url 可试听。
export interface BgmTrack {
  track_id: string;
  name: string;
  duration_sec: number;
  preview_url: string;
  license: string;
}
export interface BgmLibraryResponse {
  items: BgmTrack[];
}
export interface VideoAccepted {
  id: string;
  status: string;
}

// POST /videos/estimate → 预计积分（"确定生成"确认窗用）；请求体同 CreateVideoRequest。
export interface EstimateResponse {
  estimated_credits: number;
  unit: string;
  note?: string;
}

// 音色来源 (BRAND-VOICE-UI-0001 §8)：系统预设 / 品牌音色(声音克隆)。
export type VoiceSource = "preset" | "brand_voice";
export interface Voice {
  id: string;
  provider: string;
  voice_code: string;
  display_name: string;
  gender: string | null;
  language: string | null;
  sample_url?: string | null;
  // VoiceRead.source：口播 picker 按 source==="brand_voice" 分组「我的品牌音色」(对齐后端 §8)。
  source?: VoiceSource;
}
export interface AvatarPreset {
  asset_id: string;
  display_name: string;
  thumbnail_url: string | null; // backend str|None (P2-3)
}

// 文案字数档位（ECOM-VIDEO-OPTIMIZE-UI-0001 契约 §4.5）：短/中/长，目标字数区间由 BE prompt 控制。
export type ScriptLengthTier = "short" | "medium" | "long";

export interface ScriptGenerateRequest {
  topic: string;
  video_mode?: string; // 电商带货传 "seedance_i2v"；让文案口径/长度随模式
  duration_sec?: number; // 目标时长（秒）：文案长度随之，与视频/字幕对齐
  length_tier?: ScriptLengthTier; // 电商带货文案长度档位（默认 medium，契约 §4.5）；DeepSeek 不变，仅 prompt 控长
}

export interface ScriptGenerateResponse {
  script: string;
}

// POST /videos/scene-prompt 请求（ECOM-VIDEO-OPTIMIZE-UI-0001 契约 §4.2）：从只发 topic → 发产品图 keys + 文案 + topic。
// product_image_keys 必填（≥1）：luna 多模态严格纪律「必须带产品图」。
export interface ScenePromptRequest {
  topic?: string;
  script?: string;
  product_image_keys: string[];
  // ECOM-VIDEO-SCENE-DURATION-FIX-UI-0001：Cowork 冻结 §4.2 时漏了 duration_sec，致画面提示词秒数恒「约 15 秒」。
  // 补传当前选中时长（含自定义值）。可选，BE 侧夹取 [5,120]（schemas/videos.py::ScenePromptRequest._clamp_duration）。
  duration_sec?: number;
}

// POST /videos/scene-prompt → 画面提示词（电商带货 i2v "AI 生成画面" 用）。
export interface ScenePromptResponse {
  scene_prompt: string;
  negative_prompt: string; // 新增（契约 §4.2/req6）：luna 同产出负面提示词，前端自动填入负面框
}

export interface UploadImageResponse {
  asset_id: string;
  type: "avatar_image";
  status: "ready";
  thumbnail_url?: string | null;
}

// POST /uploads/videos（数字人出镜视频源，AVATAR-VIDEO-SOURCE-UI-0001）：返回 asset_id 供 avatar_video_asset_id。
// 形状以 BE 包(AVATAR-VIDEO-SOURCE-BE-0001)为准；mock 先行。
export interface AvatarVideoUploadResponse {
  asset_id: string;
  type?: string;
  status?: string;
}

// POST /uploads（电商带货 i2v 产品图）：返回的 `key` 即提交体使用的 image_key。
export interface UploadResponse {
  key: string;
  uri?: string;
  content_type?: string;
  size?: number;
}

export interface Quota {
  total: number;
  used: number;
  reserved: number;
  remaining: number;
}

// SSE 新枚举帧（§8）+ 旧帧兜底字段
export interface VideoEvent {
  status?: VideoStatus | string;
  progress?: number; // 新帧 0..100 number；旧帧 0..1 小数
  step?: string | null; // tts|avatar|subtitle|compose|upload
  playback_url?: string | null;
  download_url?: string | null;
  thumbnail_url?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  /**
   * 心跳帧（GEN-HEARTBEAT-UI-0001 · FIX2 真联调坐实）：生成期间周期推送，
   * **`progress` 与 `stage`/`step` 保持不变**，只证明"链路还活着"。
   * 前端必须显式识别它来重置 stall 时钟——否则会被 applyEvent「只认真实前进」的判据直接忽略。
   *
   * 🔴 真形状（BE #222 实测，非契约转述）：**ISO8601 字符串**，Python `datetime.now(UTC).isoformat()`
   * 产出 `"2026-07-25T13:16:56.439672+00:00"`（6 位微秒 + `+00:00` 偏移，**不是** `Z` 结尾、
   * **更不是** epoch 毫秒——冻结 §四 写的"ISO8601 或 epoch ms"二选一，实际只有前者）。
   * 🔴 无心跳时 **整个键不出现**（`videos.py:1489` 只在 `is not None` 时才写入），故此处是可选而非可空。
   */
  heartbeat_at?: string;
  // 旧帧兜底
  task_id?: string;
  stage?: string | null;
  error?: string | null;
  timeout_seconds?: number;
}

// ── 文案仿写 + 标题/话题生成 (COPY-UI-0001) ─────────────────────────────
// 同步 REST（不进 Celery/SSE）；内层 data，外层 M2 封套不变。对齐 seam §2。
export type CopyMode = "smart" | "custom" | "auto";
export type CopyPlatform = "douyin" | "xiaohongshu";
export type CopyGenerationOperation = "rewrite" | "titles" | "topics";

// POST /api/v1/copy/estimate（无请求体）
export interface CopyEstimateBreakdownItem {
  operation: CopyGenerationOperation;
  estimated_credits: number; // BE: int >= 0
}
export interface CopyEstimateResponse {
  estimated_credits: number; // BE: int >= 0；三项 breakdown 之和
  unit: "credits";
  note: string | null;
  breakdown: CopyEstimateBreakdownItem[];
}

// POST /api/v1/copy/rewrite
export interface CopyRewriteRequest {
  source_text: string; // 必填非空，≤4000 字（超长 422）
  mode: CopyMode; // 智能 / 自定义 / 自动
  instruction?: string; // mode=custom 必填，其余忽略
  n?: number; // mode=auto 多候选条数，默认 3，clamp 1..5
  target_platform?: CopyPlatform | null; // 可选，影响风格语气
  video_mode?: string; // 串联口播/带货时按风格 + clean_spoken_script 清洗
  duration_sec?: number; // 串联时按时长控字数
}
export interface CopyRewriteResult {
  text: string;
}

/**
 * 每个文案端点的成败标记（BE PR #239 `e2bc2c02`：`schemas/response.py OperationOutcome`
 * + `schemas/copy.py CopyGenerationOutcome`）。**成功响应必带**（BE 是必填字段），
 * 失败响应则由 `core/exceptions.py:_copy_generation_error_outcome` 按 URL 后缀推断后
 * 挂在 `error.outcome` 上。
 *
 * 🔴 前端**不拿它当成败主判据**，主判据仍是 `Promise.allSettled` 的 fulfilled/rejected
 *   （见 copywriting-form.tsx `onGenerate`）。理由：网络中断 / 超时 / 5xx 无响应体时**根本没有
 *    outcome 可读**，靠它判会把这些情形漏成"成功"。此处保留它是为了①类型与 BE 契约一致、
 *    ②mock 必须照发（不比 BE 松），③日后若出现"HTTP 200 但业务失败"的端点可直接接。
 */
export interface CopyGenerationOutcome {
  operation: CopyGenerationOperation;
  status: "succeeded" | "failed";
}

export interface CopyRewriteResponse {
  results: CopyRewriteResult[]; // smart/custom 返 1 条；auto 返 n 条
  outcome: CopyGenerationOutcome;
}

// POST /api/v1/copy/titles
export interface CopyTitlesRequest {
  source_text: string;
  n?: number; // 默认 5
  style?: string | null; // 短句 / 长句（二字/四字 backlog）
}
export interface CopyTitlesResponse {
  titles: string[];
  outcome: CopyGenerationOutcome;
}

// POST /api/v1/copy/topics
export interface CopyTopicsRequest {
  source_text: string;
  n?: number; // 默认 5
}
export interface CopyTopicsResponse {
  topics: string[]; // 带 # 标签
  outcome: CopyGenerationOutcome;
}

// 历史草稿持久化（存历史「文案」tab）
export interface CopyDraftCreateRequest {
  source_text: string;
  result_text: string; // 最终（用户可能手改过的）改写文案
  titles?: string[];
  topics?: string[];
  mode: CopyMode;
  target_platform?: CopyPlatform | null;
}
export interface CopyDraft {
  id: string;
  source_text: string;
  result_text: string;
  titles?: string[] | null;
  topics?: string[] | null;
  mode: CopyMode;
  target_platform?: string | null;
  created_at: string;
}
export interface CopyDraftListResponse {
  items: CopyDraft[];
  total: number;
}

// ── 口播生产力增强 v1 (ORAL-PROD-UI-0001) — 字幕样式 + 封面 ──
export type SubtitlePosition = "top" | "center" | "bottom";

// GET /oral/subtitle-templates 的内层条目（5 套静态预设）
export interface SubtitleTemplate {
  id: string;
  name: string;
  font_family: string;
  font_size: number;
  color: string;
  stroke_color: string | null;
  stroke_width: number;
  background: string | null;
  position: SubtitlePosition;
}
export interface SubtitleTemplatesResponse {
  templates: SubtitleTemplate[];
}

// 并入口播生成请求的字幕样式覆盖；template_id 必填，其余覆盖项留空则继承预设
export interface SubtitleStyle {
  template_id: string;
  font_family?: string;
  font_size?: number; // clamp 16..96
  color?: string; // #RRGGBB
  position?: SubtitlePosition;
}

// GET /covers/frame-candidates?video_task_id=&count= 的内层
export interface FrameCandidate {
  timestamp_sec: number;
  preview_url: string;
}
export interface FrameCandidatesResponse {
  frames: FrameCandidate[];
}

// POST /covers/from-frame
export interface CoverTitle {
  text: string; // 可空 → 纯截帧不叠字
  font_size?: number; // clamp 24..120
  color?: string; // #RRGGBB
  position?: SubtitlePosition;
}
export interface CoverFromFrameRequest {
  video_task_id: string;
  timestamp_sec: number;
  title: CoverTitle;
  layout_template_id?: string; // v1 预留
}
export interface Cover {
  id: string;
  image_url: string;
  width: number;
  height: number;
}
export interface CoverFromFrameResponse {
  cover: Cover;
}

// 历史删除 / 清空 (HIST-UI-0001)
export interface DeleteResult {
  deleted: boolean;
}
export interface ClearResult {
  deleted_count: number;
}

// ── 电商图扩展 Phase1 (ECOM-IMG-UI-0001) — 白底图/抠图(单张 + 批量) ──
export type CutoutBackground = "white" | "transparent";

// POST /ecom-images/cutout（内部创建 photo VideoTask kind=ecom_cutout，返 task_id 供轮询）
export interface CutoutRequest {
  source_asset_id: string;
  background: CutoutBackground;
  aspect_ratio?: string; // 画面比例（IMAGE-ASPECT-RATIO-UI-0001；默认 1:1，对齐 BE RequestedImageAspectRatio）
  apply_visible_label?: boolean; // AI 显式标识开关（LABEL-TOGGLE-UI-0001，默认关）
}
export interface CutoutResponse {
  task_id: string;
  status: string;
}

// POST /ecom-images/cutout/batch（fan-out N clamp 1..20）
export interface CutoutBatchItem {
  source_asset_id: string;
  background: CutoutBackground;
  aspect_ratio?: string; // 画面比例（每项独立，后端 batch item = EcomCutoutRequest）
  apply_visible_label?: boolean; // 批量每项独立标识（后端 batch item = EcomCutoutRequest）
}
export interface CutoutBatchRequest {
  items: CutoutBatchItem[];
}
export interface CutoutBatchTask {
  task_id: string;
  source_asset_id: string;
  status: string;
}
export interface CutoutBatchResponse {
  batch_id: string;
  tasks: CutoutBatchTask[];
}

// ── 电商图扩展 Phase2 (ECOM-MODEL-UI-0001) — AI 模特(单张 + 批量) ──
export type ModelGender = "female" | "male" | "any";

// GET /ecom-images/model-styles（风格预设列表；后端 EcomModelStyle 仅 id+name）
export interface ModelStyle {
  id: string;
  name: string;
}
export interface ModelStylesResponse {
  styles: ModelStyle[];
}

// 商品图组合语义（ECOM-MODEL-OPTIMIZE-UI-0001 · D2）：
//  multi_angle=同一件商品的多角度；multi_item=同一模特上身多件商品（衣裤鞋饰等，默认）。
export type ProductImagesMode = "multi_angle" | "multi_item";

// POST /ecom-images/model（内部创建 photo VideoTask kind=ecom_model，返 task_id 供轮询）
// 字段名对齐后端 EcomModelRequest(extra="forbid")：自定义补充 = extra_prompt（非 custom_prompt，否则 422）。
// ECOM-MODEL-OPTIMIZE-UI-0001（D1–D4）：商品图转多张 + 新增模特图 + 组合语义 + 风格可选/自定义 + 取消补充字数限制。
export interface ModelRequest {
  product_asset_ids: string[]; // 商品图 asset_id（1–N；受「商品+模特合计 ≤6」约束，D1）
  model_asset_ids?: string[]; // 模特图 asset_id（0–N；不传=纯文生模特，D1）
  product_images_mode: ProductImagesMode; // 商品图组合语义（默认 multi_item，D2）
  gender: ModelGender;
  style_id?: string; // 风格预设 id（改为可选；与 custom_style 互斥，D3；未知 id → 422 ECOM_MODEL_STYLE_INVALID）
  custom_style?: string; // 自定义风格（min 1、**≤20000** 反滥用上界；与 style_id 互斥，D3）
  // 🔴 D4：自定义补充取消旧 200 静默截断。此前注释「后端无长度限制」是错的（route 层曾静默截断到 200，本期移除）。
  //   FIX1 真联调订正：#210 合并源 schemas/ecom_images.py:12,69 给 extra_prompt/custom_style 各 **≤20000 字符**
  //   反滥用上界（Field max_length + extra=forbid，超限 → 422 非静默截断）。前端不设 maxLength（正常远不及；此为诚实注释）。
  extra_prompt?: string;
  aspect_ratio?: string; // 画面比例（IMAGE-ASPECT-RATIO-UI-0001；默认 1:1）
  apply_visible_label?: boolean; // AI 显式标识开关（LABEL-TOGGLE-UI-0001，默认关）
  source_asset_id?: string; // 兼容保留（批量端点 item / 既有标量调用方仍用；单张新表单不发）
}
export interface ModelResponse {
  task_id: string;
  status: string;
}

// POST /ecom-images/model/batch（fan-out N clamp 1..20）。
// ⚠️ ECOM-MODEL-OPTIMIZE-UI-0001：单张 /model 已转多图新契约（product_asset_ids…），但**批量 item 刻意保留旧标量形态**
//   （§四「标量保留兼容——批量端点 item 与既有调用方」）——每项仍是「1 商品图 → 1 任务」的 fan-out。本期新表单是
//   单张 /model 的唯一调用方、不走批量；批量端点（含 useModelBatch）保留供兼容/未来（D5 批量换模特单独立项，不在本期）。
export interface ModelBatchItem {
  source_asset_id: string;
  gender: ModelGender;
  style_id: string;
  extra_prompt?: string;
  aspect_ratio?: string; // 画面比例（每项独立）
  apply_visible_label?: boolean; // 批量每项独立标识
}
export interface ModelBatchRequest {
  items: ModelBatchItem[];
}
export interface ModelBatchTask {
  task_id: string;
  source_asset_id: string;
  status: string;
}
export interface ModelBatchResponse {
  batch_id: string;
  tasks: ModelBatchTask[];
}

// ── 电商图扩展 Phase3 (ECOM-POSTER-UI-0001) — 营销海报(单张 + 批量) ──
// 对齐后端 EcomPosterRequest(extra="forbid")：template_id/title/subtitle 字段名与后端一致。
// title/subtitle 为后端必填 key(str,无 min_length→空串允许)，故前端始终发送字符串(空则 "")，
// 不可省略 key(否则 422)；UI 上仍可留空(可选填写)。
// GET /ecom-images/poster-templates（版式预设列表；仅 id+name）
export interface PosterTemplate {
  id: string;
  name: string;
}
export interface PosterTemplatesResponse {
  templates: PosterTemplate[];
}

// POST /ecom-images/poster（内部创建 photo VideoTask kind=ecom_poster，返 task_id 供轮询）
export interface PosterRequest {
  source_asset_id: string;
  template_id: string; // 版式预设，必选
  title: string; // 标题，后端必填(空串允许)，前端 UI ≤30
  subtitle: string; // 自定义一行，后端必填(空串允许)，前端 UI ≤40
  apply_visible_label?: boolean; // AI 显式标识开关（LABEL-TOGGLE-UI-0001，默认关）
}
export interface PosterResponse {
  task_id: string;
  status: string;
}

// POST /ecom-images/poster/batch（fan-out N clamp 1..20）
export interface PosterBatchItem {
  source_asset_id: string;
  template_id: string;
  title: string;
  subtitle: string;
  apply_visible_label?: boolean; // 批量每项独立标识（后端 batch item = EcomPosterRequest）
}
export interface PosterBatchRequest {
  items: PosterBatchItem[];
}
export interface PosterBatchTask {
  task_id: string;
  source_asset_id: string;
  status: string;
}
export interface PosterBatchResponse {
  batch_id: string;
  tasks: PosterBatchTask[];
}

// ── 品牌音色 / 声音克隆 (BRAND-VOICE-UI-0001，FIX1 对齐后端 §8 真契约) ──
export type BrandVoiceStatus = "processing" | "ready" | "failed"; // 处理中 / 可用 / 失败

// 声音复刻通路（BRAND-VOICE-PICKER-UI-0001）：doubao（豆包）/ cosyvoice（免费通路 COSYVOICE-CLONE-0001）。
// 字符串宽松兼容未来通路；**provider 可选**——接口暂无该字段时前端不显徽标、不报错（UI 可先于后端合并）。
export type BrandVoiceProvider = "doubao" | "cosyvoice";

// BrandVoiceRead：后端仅返 id/name/status/created_at（无 sample_url/error_message）；
// provider 为 COSYVOICE-CLONE-0001 合并后新增，**当前可能缺省**，故 optional。
export interface BrandVoice {
  id: string;
  name: string;
  status: BrandVoiceStatus;
  created_at: string;
  provider?: BrandVoiceProvider | string | null;
}
export interface BrandVoiceListResponse {
  items: BrandVoice[];
  total: number;
}

// 创建三段式（§8）：先 POST /uploads/audio(multipart file) 取 asset_id，再 JSON POST /brand-voices。
// POST /uploads/audio → { asset_id }（asset type=audio）。
export interface AudioUploadResponse {
  asset_id: string;
}
// POST /brand-voices = JSON(extra=forbid)：consent_confirmed 必须进 body(StrictBool true，否则 422)。
// provider（BRAND-VOICE-PICKER-UI-0001 范围4）：克隆通路，doubao(付费)/cosyvoice(免费)。**依赖后端
// COSYVOICE-CLONE-0001 合并**（真契约 provider: Literal["doubao","cosyvoice"]="doubao"）——UI 先行、mock 已收。
export interface BrandVoiceCreateBody {
  name: string; // 1–30 非空
  source_audio_asset_id: string;
  consent_confirmed: boolean;
  provider: BrandVoiceProvider;
}
// UI 侧入参（组件持有 Blob + 名称 + 授权勾选 + 通路）；经 createBrandVoiceFromAudio 编排上传→创建。
export interface CreateBrandVoiceInput {
  name: string;
  audio: Blob; // 录音 MediaRecorder 产物 或 上传的音频文件
  consentConfirmed: boolean;
  provider: BrandVoiceProvider; // 克隆通路 doubao/cosyvoice（范围4）
}

// ── 深度合成标识设置 (LABEL-UI-0001) ──
// 注：后端 LABEL-PIPELINE 未实现，契约据 seam §3/§5 推断，待对冻结 seam + 真栈校验。
export type LabelPosition = "br" | "bl" | "tr" | "tl" | "bc"; // 右下/左下/右上/左上/底部居中

// GET/PUT /tenant/label-settings；enabled 只读恒真（合规：显式标识不可关闭）。
export interface LabelSettings {
  position: LabelPosition;
  text: string; // 1–20 非空
  enabled: boolean; // 恒 true，只读
}
// PUT 仅发可编辑字段（position/text）；enabled 由后端强制为 true，client 不发 → 结构上无法关闭。
export interface LabelSettingsUpdate {
  position: LabelPosition;
  text: string;
}

// ── 发布中心 (PUBLISH-UI-0001) — 逐字对齐后端 backend/app/schemas/publish.py ──
export type PublishPlatformId = "douyin" | "kuaishou" | "wxchannels" | "xiaohongshu" | "bilibili";
export type PublishSourceKind = "video" | "image"; // 产物类型(后端 Literal)
export type PublishStatus = "draft" | "copied" | "published"; // 草稿 / 已复制 / 已发布

// GET /publish/platforms（后端 PublishPlatformRead）
export interface PublishPlatform {
  id: PublishPlatformId;
  name: string;
  title_max?: number;
  body_max?: number;
  hashtag_max?: number;
  publish_url?: string;
  cover_ratio?: string;
  notes?: string;
}
export interface PublishPlatformsResponse {
  items: PublishPlatform[];
}

// POST /publish/drafts → { id(记录), items:[单平台可编辑内容] }（后端 PublishDraftCreateResponse）。
export interface CreateDraftsRequest {
  source_kind: PublishSourceKind;
  source_task_id: string; // 1–80 非空
  platforms: PublishPlatformId[]; // 1–5，唯一
}
export interface PublishDraftItem {
  platform_id: PublishPlatformId;
  title: string;
  body: string; // 文案
  hashtags: string[]; // 话题
  cover_url: string;
  media_url: string; // 成片
  publish_url: string; // 「去XX发布」window.open 的公开上传页 URL
}
export interface CreateDraftsResponse {
  id: string; // 发布记录 id（PATCH 标记单平台用）
  items: PublishDraftItem[];
}

// GET /publish/records → 记录为嵌套模型：一条记录(产物) 含多平台 platforms[]{platform_id,status}。
export interface PublishRecordPlatform {
  platform_id: PublishPlatformId;
  status: PublishStatus;
}
export interface PublishRecord {
  id: string;
  source_kind: PublishSourceKind;
  source_task_id: string;
  created_at: string;
  platforms: PublishRecordPlatform[];
}
export interface PublishRecordsResponse {
  items: PublishRecord[];
  total: number;
}
// PATCH /publish/records/{record_id}：标记某记录下某平台为已发布（status 仅 "published"）。
export interface MarkPublishedRequest {
  platform_id: PublishPlatformId;
  status: "published";
}

// ── 批量生产中心 (BATCH-PROD-UI-0001) ── 据 seam《批量生产-方案与API契约冻结-20260702》。
// 后端 BATCH-PROD-0001 未合 → 契约据 seam 推断，合后对齐实际 schema + 真栈自查。
export type BatchKind = "ecom_table" | "prompt_set";
export type BatchStatus = "running" | "completed" | "partial_failed" | "failed" | "cancelled";

// 行契约（按 kind）
export interface EcomTableRow {
  product_name: string;
  selling_points: string;
  image_asset_id?: string; // 本地图上传→素材接口换 asset_id
  image_url?: string; // 表内外链 URL（后端下载转存）
}
export interface PromptSetRow {
  prompt: string;
  image_asset_id?: string; // 逐行配对参考图（BATCH-PROD-UI-0002）：开启配对时第 N 行↔第 N 图；共享模式不带
}
export type BatchRow = EcomTableRow | PromptSetRow;

// 公共参数（common）—— 逐字对齐后端 BatchCommonParams(extra=forbid)：仅允许以下 11 字段，多传 422。
export interface BatchCommon {
  video_mode?: string; // 必填(提交时)：ecom_table→seedance_i2v / prompt_set→video_gen（后端 model_validator 强校验）
  duration_sec?: number;
  resolution?: string; // 480p | 720p | 1080p（默认 720p）
  reference_image_asset_ids?: string[]; // prompt_set 参考图
  bgm?: VideoGenBgm; // 同单条 BgmSelectionRequest 形状
  voice_id?: string;
  speed?: number; // 0.5–2.0
  aspect_ratio?: string; // 9:16 | 16:9 | 1:1（默认 9:16）
  subtitle_enabled?: boolean; // 默认 true
  apply_visible_label?: boolean; // AI 标识开关（默认关，复用 LABEL-TOGGLE）
  size?: string;
}

// POST /batches/estimate + POST /batches 共用请求体
export interface BatchRequest {
  kind: BatchKind;
  rows: BatchRow[]; // ≤30
  common: BatchCommon;
}
// POST /batches/estimate → 预估
export interface BatchEstimateResponse {
  total_rows: number;
  per_row_credits: number;
  total_credits: number;
  insufficient: boolean;
  balance_credits: number;
}
// POST /batches → 创建（余额不足 422 code=INSUFFICIENT_CREDITS）
export interface BatchCreateResponse {
  batch_id: string;
  task_ids: string[];
}
// 批次聚合（BatchSummary：列表项 + 详情的 batch）—— 逐字对齐后端。
export interface BatchJob {
  id: string;
  kind: BatchKind;
  status: BatchStatus;
  total: number;
  succeeded: number;
  failed: number;
  common_params: Record<string, unknown>; // 创建时的 common 快照
  created_at: string;
  updated_at: string;
}
export interface BatchListResponse {
  items: BatchJob[];
  total: number;
}
// GET /batches/{id} → 详情（组视图轮询 ≥5s）
export interface BatchTask {
  task_id: string;
  row_index: number;
  status: string; // queued | running | done | failed | cancelled
  video_url?: string | null;
  error?: string | null;
  error_code?: string | null;
  error_message?: string | null;
}
export interface BatchDetail {
  batch: BatchJob;
  tasks: BatchTask[];
}
// POST /batches/{id}/cancel → 逐字对齐后端 BatchCancelResponse。
export interface BatchCancelResponse {
  batch_id: string;
  cancelled: number;
  running: number;
}

// ── 管理员数据看板 (ANALYTICS-UI-0001) ── 逐字对齐后端 ANALYTICS-0001 schemas（/api/v1/admin/analytics/*，require_admin）。
export type AnalyticsTenantSort =
  | "credits_desc"
  | "credits_asc"
  | "cost_desc"
  | "cost_asc"
  | "task_count_desc"
  | "task_count_asc"
  | "success_rate_desc"
  | "success_rate_asc";
export type AnalyticsGranularity = "day" | "week";

export interface AnalyticsPeriod {
  from: string; // YYYY-MM-DD（后端 alias "from"）
  to: string;
}
export interface AnalyticsOverview {
  total_credits_used: number;
  total_cost_cents: number;
  task_count: number; // 成功+失败 VideoTask 数
  success_count: number;
  failed_count: number;
  tenant_count: number;
  period: AnalyticsPeriod;
}
export interface AnalyticsBalance {
  total: number;
  used: number;
  reserved: number;
  remaining: number;
}
export interface AnalyticsTenantItem {
  tenant_id: string;
  tenant_name: string;
  credits_used: number;
  cost_cents: number;
  task_count: number;
  success_rate: number; // 0–1 浮点（展示 ×100%）
  balance: AnalyticsBalance;
}
export interface AnalyticsByTenant {
  items: AnalyticsTenantItem[];
  total: number;
}
export interface AnalyticsProviderItem {
  provider: string;
  model: string | null;
  credits_used: number;
  cost_cents: number;
  task_count: number; // ⚠️ 计费笔数：count(UsageRecord)，含文案/图片等无 VideoTask 的记录，非「视频任务数」
  share_pct: number;
}
export interface AnalyticsByProvider {
  items: AnalyticsProviderItem[];
}
export interface AnalyticsTimeseriesBucket {
  date: string; // YYYY-MM-DD
  credits_used: number;
  cost_cents: number;
  task_count: number;
}
export interface AnalyticsTimeseries {
  buckets: AnalyticsTimeseriesBucket[];
}

// ── Authoritative billing confirmation + operation recovery ──
// Decimal strings and payable_credits are server-owned display values. Callers must
// never derive or round prices from these fields in JavaScript.
export interface BillingConfirmation {
  quote_token: string;
  idempotency_key: string;
}

export interface BillingSummary {
  operation_id: string;
  idempotency_key: string;
  status: "reserved" | "settled" | "partially_settled" | "released";
  requested_credits: number;
  held_credits: number;
  settled_credits: number;
  released_credits: number;
}

export interface BillingPricingLine {
  operation: string;
  capability: string;
  unit: string;
  quantity: string;
  unit_credits: string;
  subtotal_credits: string;
  rate_scope: "tenant_overridable" | "platform_fixed";
  rate_source: "tenant_rate" | "platform_rate" | "code_default" | "fixed_policy";
  rate_id: string | null;
  effective_at: string | null;
  policy_key: string | null;
  policy_version: number | null;
  label: string;
}

export interface BillingDisclosure {
  key: string;
  rendered_text: string;
  copy_version: number;
  unit: string;
  rate_scope: BillingPricingLine["rate_scope"];
  rate_source: BillingPricingLine["rate_source"];
  rate_id: string | null;
  effective_at: string | null;
  policy_key: string | null;
  policy_version: number | null;
  reference_unit_credits: string;
}

interface BillingQuoteBase {
  pricing_contract: "billing_quote";
  operation: string;
  subtotal_credits: string;
  payable_credits: number;
  disclosures: BillingDisclosure[];
  quote_token: string;
  expires_at: string;
}

export type BillingQuote =
  | (BillingQuoteBase & {
      pricing_shape: "simple";
      unit: string;
      quantity: string;
      unit_credits: string;
      rate_scope: BillingPricingLine["rate_scope"];
      rate_source: BillingPricingLine["rate_source"];
      breakdown: [];
    })
  | (BillingQuoteBase & {
      pricing_shape: "composite";
      unit: null;
      quantity: null;
      unit_credits: null;
      rate_scope: null;
      rate_source: null;
      breakdown: [BillingPricingLine, ...BillingPricingLine[]];
    });

export type BillingLookupPayload = Record<string, unknown>;

export interface BillingScriptResult extends BillingLookupPayload {
  script: string;
}

export interface BillingScenePromptResult extends BillingLookupPayload {
  scene_prompt: string;
  negative_prompt: string;
}

interface BillingEcomImageBatchItemBase extends BillingLookupPayload {
  item_index: number;
  task_id: string;
  source_asset_id: string;
}

export type BillingEcomImageBatchItem = BillingEcomImageBatchItemBase &
  ({ status: "done"; asset_id: string } | { status: "failed"; asset_id: null });

export interface BillingEcomImageBatchResult extends BillingLookupPayload {
  items: BillingEcomImageBatchItem[];
}

interface BillingVideoTaskResourceBase extends BillingLookupPayload {
  task_id: string;
}

export type BillingVideoTaskResource = BillingVideoTaskResourceBase &
  ({ status: "queued" } | { status: "done" });

export interface BillingBrandVoiceResource extends BillingLookupPayload {
  id: string;
  name: string;
  provider: "cosyvoice-voice-clone";
  status: "ready";
  order_status: null;
  delivery_status: "active";
  created_at: string;
}

interface BillingBrandVoiceOrderBase extends BillingLookupPayload {
  id: string;
  tenant_id: string;
  ordered_by_user_id: string;
  order_type: "create" | "renew";
  requested_name: string;
  source_audio_asset_id: string;
  existing_brand_voice_id: string | null;
  created_at: string;
  updated_at: string;
  billing: BillingSummary;
}

export type BillingAwaitingBrandVoiceOrderResource = BillingBrandVoiceOrderBase & {
  status: "awaiting_fulfillment";
  fulfilled_brand_voice_id: null;
  fulfilled_provider_voice_id: null;
  rejection_reason: null;
  fulfilled_at: null;
  rejected_at: null;
  refund_disposition: "not_applicable";
  refund_grant_status: null;
  refund_applied_at: null;
};

export type BillingFulfilledBrandVoiceOrderResource = BillingBrandVoiceOrderBase & {
  status: "fulfilled";
  fulfilled_brand_voice_id: string;
  fulfilled_provider_voice_id: string;
  rejection_reason: null;
  fulfilled_at: string;
  rejected_at: null;
  refund_disposition: "not_applicable";
  refund_grant_status: null;
  refund_applied_at: null;
};

type BillingRejectedRefund =
  | {
      refund_disposition: "source_subscription_released";
      refund_grant_status: null;
      refund_applied_at: null;
    }
  | {
      refund_disposition: "current_subscription_credited";
      refund_grant_status: "applied";
      refund_applied_at: string;
    }
  | {
      refund_disposition: "pending_next_subscription";
      refund_grant_status: "pending";
      refund_applied_at: null;
    };

export type BillingRejectedBrandVoiceOrderResource = BillingBrandVoiceOrderBase & {
  status: "rejected";
  fulfilled_brand_voice_id: null;
  fulfilled_provider_voice_id: null;
  rejection_reason: string;
  fulfilled_at: null;
  rejected_at: string;
} & BillingRejectedRefund;

export type BillingBrandVoiceOrderResource =
  | BillingAwaitingBrandVoiceOrderResource
  | BillingFulfilledBrandVoiceOrderResource
  | BillingRejectedBrandVoiceOrderResource;

export interface BillingLookupPayloadMap {
  script_generate_result: BillingScriptResult;
  scene_prompt_result: BillingScenePromptResult;
  ecom_image_batch: BillingEcomImageBatchResult;
  video_task: BillingVideoTaskResource;
  brand_voice_order: BillingBrandVoiceOrderResource;
  brand_voice: BillingBrandVoiceResource;
}

export type BillingKnownResultType = keyof BillingLookupPayloadMap;

export type BillingKnownOperation =
  | "script_generate"
  | "scene_prompt"
  | "ecom_cutout"
  | "ecom_model"
  | "video_create"
  | "doubao_brand_voice_order_create"
  | "doubao_brand_voice_order_renew"
  | "cosyvoice_brand_voice_create";

type BillingOperationForResult<K extends BillingKnownResultType> =
  K extends "script_generate_result"
    ? "script_generate"
    : K extends "scene_prompt_result"
      ? "scene_prompt"
      : K extends "ecom_image_batch"
        ? "ecom_cutout" | "ecom_model"
        : K extends "video_task"
          ? "video_create"
          : K extends "brand_voice_order"
            ? "doubao_brand_voice_order_create" | "doubao_brand_voice_order_renew"
            : "cosyvoice_brand_voice_create";

interface BillingInProgressPayloadMap {
  script_generate_result: BillingScriptResult;
  scene_prompt_result: BillingScenePromptResult;
  ecom_image_batch: BillingEcomImageBatchResult;
  video_task: Extract<BillingVideoTaskResource, { status: "queued" }>;
  brand_voice_order: BillingAwaitingBrandVoiceOrderResource;
}

interface BillingSucceededPayloadMap {
  script_generate_result: BillingScriptResult;
  scene_prompt_result: BillingScenePromptResult;
  ecom_image_batch: BillingEcomImageBatchResult;
  video_task: Extract<BillingVideoTaskResource, { status: "done" }>;
  brand_voice_order: BillingFulfilledBrandVoiceOrderResource;
  brand_voice: BillingBrandVoiceResource;
}

type BillingResultId<K extends BillingKnownResultType> =
  K extends "script_generate_result" | "scene_prompt_result" ? null : string;

type BillingSucceededResource<K extends BillingKnownResultType> =
  BillingResultId<K> extends null ? null : BillingSucceededPayloadMap[K];

interface BillingOperationLookupBase {
  idempotency_key: string;
  billing: BillingSummary;
  result_id: string | null;
}

type BillingInProgressLookup =
  | (BillingOperationLookupBase & {
      operation: BillingKnownOperation;
      state: "in_progress";
      completion_kind: null;
      result_type: null;
      result_id: null;
      resource: null;
      result: null;
      failure: null;
    })
  | (BillingOperationLookupBase & {
      operation: "cosyvoice_brand_voice_create";
      state: "in_progress";
      completion_kind: null;
      result_type: null;
      result_id: string;
      resource: null;
      result: null;
      failure: null;
    })
  | {
      [K in keyof BillingInProgressPayloadMap]: BillingOperationLookupBase & {
        operation: BillingOperationForResult<K>;
        state: "in_progress";
        completion_kind: null;
        result_type: K;
        result_id: BillingResultId<K>;
        resource: BillingInProgressPayloadMap[K] | null;
        result: null;
        failure: null;
      };
    }[keyof BillingInProgressPayloadMap];

type BillingSucceededLookup = {
  [K in BillingKnownResultType]: BillingOperationLookupBase & {
    operation: BillingOperationForResult<K>;
    state: "completed";
    completion_kind: "succeeded";
    result_type: K;
    result_id: BillingResultId<K>;
    resource: BillingSucceededResource<K>;
    result: BillingSucceededPayloadMap[K];
    failure: null;
  };
}[BillingKnownResultType];

export type BillingOperationLookup =
  | BillingInProgressLookup
  | BillingSucceededLookup
  | (BillingOperationLookupBase & {
      operation: "doubao_brand_voice_order_create" | "doubao_brand_voice_order_renew";
      state: "completed";
      completion_kind: "rejected";
      result_type: "brand_voice_order";
      result_id: string;
      resource: BillingRejectedBrandVoiceOrderResource;
      result: null;
      failure: null;
    })
  | (BillingOperationLookupBase & {
      operation: BillingKnownOperation;
      state: "completed";
      completion_kind: "failed";
      result_type: null;
      result_id: null;
      resource: null;
      result: null;
      failure: {
        code: string;
        original_http_status: number;
        detail: { requires_new_quote: boolean | null } | null;
      };
    });

type BillingOperationLookupForMember<
  TLookup,
  TOperation extends BillingKnownOperation
> = TLookup extends { operation: infer SupportedOperation extends BillingKnownOperation }
  ? TOperation extends SupportedOperation
    ? Omit<TLookup, "operation"> & { operation: TOperation }
    : never
  : never;

export type BillingOperationLookupFor<TOperation extends BillingKnownOperation> =
  TOperation extends BillingKnownOperation
    ? BillingOperationLookupForMember<BillingOperationLookup, TOperation>
    : never;

/** Complete canonical operation-to-lookup mapping used by parser-free billing APIs. */
export type BillingOperationLookupMap = {
  readonly [TOperation in BillingKnownOperation]: BillingOperationLookupFor<TOperation>;
};

/** Only returned when a caller explicitly supplies an extension registry/parser. */
export type ExtendedBillingOperationLookup =
  | (BillingOperationLookupBase & {
      operation: string;
      state: "in_progress";
      completion_kind: null;
      result_type: string;
      resource: BillingLookupPayload | null;
      result: null;
      failure: null;
    })
  | (BillingOperationLookupBase & {
      operation: string;
      state: "completed";
      completion_kind: "succeeded";
      result_type: string;
      resource: BillingLookupPayload | null;
      result: BillingLookupPayload;
      failure: null;
    })
  | (BillingOperationLookupBase & {
      operation: string;
      state: "completed";
      completion_kind: "rejected";
      result_type: string;
      resource: BillingLookupPayload;
      result: null;
      failure: null;
    })
  | (BillingOperationLookupBase & {
      operation: string;
      state: "completed";
      completion_kind: "failed";
      result_type: null;
      result_id: null;
      resource: null;
      result: null;
      failure: {
        code: string;
        original_http_status: number;
        detail: { requires_new_quote: boolean | null } | null;
      };
    });
