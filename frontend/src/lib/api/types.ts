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
  topic: string;
  script: string;
  voice_id: string;
  aspect_ratio: string;
  subtitle_enabled: boolean;
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
  topic: string; // 必填 ≤500
  script?: string; // 可选；缺则后端 DeepSeek 生成（前端流程会带）
  voice_id: string; // 必填
  avatar_asset_id: string; // 必填（上传或预设产出的 asset_id）
  speed?: number; // 默认 1.0
  aspect_ratio?: string; // 默认 "9:16"
  subtitle_enabled?: boolean; // 默认 true
}
export interface VideoAccepted {
  id: string;
  status: string;
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
  thumbnail_url: string;
}

export interface ScriptGenerateResponse {
  script: string;
}

export interface UploadImageResponse {
  asset_id: string;
  type: "avatar_image";
  status: "ready";
  thumbnail_url?: string | null;
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
  progress?: number; // 新帧 0..100 int；旧帧 0..1 小数
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
