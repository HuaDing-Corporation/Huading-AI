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

export type VideoStatus = "queued" | "running" | "done" | "failed";

export interface VideoListItem {
  id: string;
  status: VideoStatus;
  progress: number; // 0..100
  topic: string;
  mode?: string | null; // avatar_talk | seedance_i2v | photo —结果渲染：视频 vs 图
  kind?: string | null; // 图片细分：如 "cover"（封面 photo task；HIST kind 筛真后端支持）
  error_code?: string | null; // 图片失败时映射友好文案（friendlyImageError）
  thumbnail_url?: string | null;
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
  topic: string; // 必填 ≤500（电商带货=产品卖点/主题）
  script?: string; // 可选；缺则后端 DeepSeek 生成（前端流程会带）
  voice_id?: string; // 数字人口播 / 电商带货必填；照片 photo 不传（无配音）
  avatar_asset_id?: string; // 数字人口播必填（上传/预设产出的 asset_id）；i2v 不传
  video_mode?: string; // 省略=数字人口播 avatar_talk；电商带货传 "seedance_i2v"
  image_key?: string; // 电商带货 i2v 必填 / 照片 photo 可选参考图，来自 POST /uploads
  scene_prompt?: string; // 电商带货 i2v 画面提示词（与口播解耦，可 AI 生成）；空则后端回退 topic
  duration_sec?: number; // 电商带货 i2v 目标时长（秒，5–120，默认 30），与后端 clamp 对齐
  image_size?: string; // 照片 photo：1024x1024 / 1536x1024 / 1024x1536
  image_quality?: string; // 照片 photo：low / medium / high（影响积分）
  speed?: number; // 默认 1.0
  aspect_ratio?: string; // 默认 "9:16"
  subtitle_enabled?: boolean; // 默认 true
  subtitle_style?: SubtitleStyle; // 数字人口播：字幕样式覆盖（ORAL-PROD-UI-0001）；缺省=与 0001 默认烧入一致（不回归）
  purpose?: string; // 照片/封面：用途标识，如 "cover"（AI 封面复用 0003 文生图标识；进图片历史作为 photo）
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

export interface Voice {
  id: string;
  provider: string;
  voice_code: string;
  display_name: string;
  gender: string | null;
  language: string | null;
  sample_url?: string | null;
}
export interface AvatarPreset {
  asset_id: string;
  display_name: string;
  thumbnail_url: string | null; // backend str|None (P2-3)
}

export interface ScriptGenerateRequest {
  topic: string;
  video_mode?: string; // 电商带货传 "seedance_i2v"；让文案口径/长度随模式
  duration_sec?: number; // 目标时长（秒）：文案长度随之，与视频/字幕对齐
}

export interface ScriptGenerateResponse {
  script: string;
}

// POST /videos/scene-prompt → 画面提示词（电商带货 i2v "AI 生成画面" 用）。
export interface ScenePromptResponse {
  scene_prompt: string;
}

export interface UploadImageResponse {
  asset_id: string;
  type: "avatar_image";
  status: "ready";
  thumbnail_url?: string | null;
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
}
export interface CutoutResponse {
  task_id: string;
  status: string;
}

// POST /ecom-images/cutout/batch（fan-out N clamp 1..20）
export interface CutoutBatchItem {
  source_asset_id: string;
  background: CutoutBackground;
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
