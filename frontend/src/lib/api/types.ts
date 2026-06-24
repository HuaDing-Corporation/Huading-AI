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
