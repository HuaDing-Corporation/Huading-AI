// Backend contract types (mirror app/schemas/*). Keep in sync with the API.

export interface ApiResponse<T> {
  data: T | null;
  error: { code: string; message: string; detail?: unknown } | null;
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

export interface VideoAccepted {
  task_id: string;
  status: string;
}

export type VideoStatus = "queued" | "running" | "done" | "failed";

/** Authoritative video record (GET /videos and GET /videos/{id}). */
export interface VideoRead {
  id: string;
  title: string;
  prompt: string;
  mode: string;
  status: VideoStatus;
  progress: number; // 0..100
  created_at: string;
  duration_sec?: number | null;
  thumbnail_url?: string | null;
  playback_url?: string | null;
  download_url?: string | null;
  error?: string | null;
}

export interface VideoListResponse {
  items: VideoRead[];
  next_cursor?: string | null;
}

/**
 * SSE progress frame. The stream still carries the worker's progress-store shape
 * (uppercase status, progress 0..1) — distinct from VideoRead — so we reconcile
 * the authoritative record (playback_url etc.) via GET /videos/{id} on terminal.
 * The timeout keep-alive reuses task_id + stage only.
 */
export interface VideoEvent {
  task_id?: string;
  status?: string; // PENDING | STARTED | PROGRESS | SUCCESS | FAILURE
  stage?: string | null;
  progress?: number; // 0..1
  error?: string | null;
  timeout_seconds?: number;
}

export type VideoMode = "static_template" | "seedance_t2v" | "seedance_i2v";

export interface CreateVideoRequest {
  topic: string;
  video_mode?: VideoMode;
  image_key?: string | null; // required for seedance_i2v (from POST /uploads)
  pipeline?: "standard" | "custom";
  mode?: "generate" | "fixed";
  n_scenes?: number;
  frame_template?: string | null;
  voice?: string | null;
  tts_speed?: number;
}

export interface UploadResponse {
  key: string; // tenant-relative, e.g. "uploads/<uuid>.jpg"
  uri: string;
  content_type: string;
  size: number;
}
