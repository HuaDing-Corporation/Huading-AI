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

/** Mirrors VideoTaskStatus; the SSE timeout event reuses task_id + stage only. */
export interface VideoTaskStatus {
  task_id: string;
  status: string; // PENDING | STARTED | PROGRESS | SUCCESS | FAILURE
  stage?: string | null;
  progress?: number; // 0..1
  frame_current?: number | null;
  frame_total?: number | null;
  video_url?: string | null;
  error?: string | null;
  timeout_seconds?: number;
}

export interface CreateVideoRequest {
  topic: string;
  pipeline?: "standard" | "custom";
  mode?: "generate" | "fixed";
  n_scenes?: number;
  frame_template?: string | null;
  voice?: string | null;
  tts_speed?: number;
}
