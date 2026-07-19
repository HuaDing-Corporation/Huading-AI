// Backend contract types (mirror app/schemas/*). Keep in sync with the API.

export interface ApiResponse<T> {
  data: T | null;
  error: { code: string; message: string; request_id?: string; detail?: unknown; details?: unknown } | null;
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
  duration_sec?: number; // 电商带货 i2v 目标时长（秒，5–120，默认 30）；视频生成限 5/10/15
  image_size?: string; // @deprecated 照片旧「尺寸」（IMAGE-ASPECT-RATIO-UI-0001 起改用 aspect_ratio；BE 仅在 aspect_ratio 省略时回退翻译）；cover 通路仍可带
  image_quality?: string; // @deprecated 照片旧「质量」（IMAGE-ASPECT-RATIO-UI-0001 起去除；BE 已忽略）；cover 通路仍可带、不影响
  speed?: number; // 默认 1.0
  aspect_ratio?: string; // 画面比例：1:1/4:3/3:2/16:9/21:9/3:4/2:3/9:16/auto（照片默认 1:1，视频默认 9:16）
  subtitle_enabled?: boolean; // 默认 true
  subtitle_style?: SubtitleStyle; // 数字人口播：字幕样式覆盖（ORAL-PROD-UI-0001）；缺省=与 0001 默认烧入一致（不回归）
  apply_visible_label?: boolean; // AI 生成显式标识开关（LABEL-TOGGLE-UI-0001）；默认关(false)，开=true。对齐后端 VideoGenerateRequest.apply_visible_label
  purpose?: string; // 照片/封面：用途标识，如 "cover"（AI 封面复用 0003 文生图标识；进图片历史作为 photo）
  // ── 图片生成/修改 photo 优化 (IMAGE-GEN-OPTIMIZE-UI-0001 契约 §四) ──
  image_keys?: string[]; // 参考图 1–6（取代标量 image_key；来自 POST /uploads→key）。可选（纯文生图不带）；image_key 保留兼容 AI 封面等既有 caller
  master_prompt?: string; // 任务总控提示词（全局风格前缀，可选，无字数上限）
  master_negative_prompt?: string; // 任务统一负面提示词（可选，无上限）——同 negative_prompt 编码进 prompt，非硬约束
  // 四个强度：均 int|None，取值 10..100 步长 10，None=未开启（默认）。底层编码进提示词（provider 无原生参数），软性倾向、非精确控制。
  similarity_strength?: number; // 图片相似度
  creativity_strength?: number; // AI 创意程度
  subject_strength?: number; // 主体保持强度
  background_strength?: number; // 背景参考强度
  // 注：图片负面提示词复用上方 negative_prompt 字段（视频链路已有；photo 分支此前不读，本期起读）。
  // ── 视频生成 video_gen (VIDEOGEN-UI-0001, seam §2) ──
  prompt?: string; // 不限字数提示词（seam 字段）；同时 topic 复用此文本作标题/展示
  reference_image_asset_ids?: string[]; // 参考图 1–9 张（POST /uploads/images → asset_id）
  resolution?: string; // 视频分辨率 "480p" | "720p" | "1080p"（默认 720p）
  bgm?: VideoGenBgm; // 可选背景音乐：上传(asset_id) 或 配乐库(track_id)
}

// 视频生成 BGM（seam §2/§3）：上传(复用 /uploads/audio→asset_id) 或 配乐库(track_id) 二选一。
export type VideoGenBgm =
  | { source: "upload"; asset_id: string }
  | { source: "library"; track_id: string };
export type VideoGenResolution = "480p" | "720p" | "1080p";
export const VIDEO_GEN_DURATIONS = [5, 10, 15] as const;
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
export interface CopyRewriteResponse {
  results: CopyRewriteResult[]; // smart/custom 返 1 条；auto 返 n 条
}

// POST /api/v1/copy/titles
export interface CopyTitlesRequest {
  source_text: string;
  n?: number; // 默认 5
  style?: string | null; // 短句 / 长句（二字/四字 backlog）
}
export interface CopyTitlesResponse {
  titles: string[];
}

// POST /api/v1/copy/topics
export interface CopyTopicsRequest {
  source_text: string;
  n?: number; // 默认 5
}
export interface CopyTopicsResponse {
  topics: string[]; // 带 # 标签
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

// POST /ecom-images/model（内部创建 photo VideoTask kind=ecom_model，返 task_id 供轮询）
// 字段名对齐后端 EcomModelRequest(extra="forbid")：自定义补充 = extra_prompt（非 custom_prompt，否则 422）。
export interface ModelRequest {
  source_asset_id: string;
  gender: ModelGender;
  style_id: string;
  extra_prompt?: string; // 自定义补充（前端 UI ≤200；后端无长度限制）
  aspect_ratio?: string; // 画面比例（IMAGE-ASPECT-RATIO-UI-0001；默认 1:1）
  apply_visible_label?: boolean; // AI 显式标识开关（LABEL-TOGGLE-UI-0001，默认关）
}
export interface ModelResponse {
  task_id: string;
  status: string;
}

// POST /ecom-images/model/batch（fan-out N clamp 1..20）
export interface ModelBatchItem {
  source_asset_id: string;
  gender: ModelGender;
  style_id: string;
  extra_prompt?: string;
  aspect_ratio?: string; // 画面比例（每项独立）
  apply_visible_label?: boolean; // 批量每项独立标识（后端 batch item = EcomModelRequest）
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
