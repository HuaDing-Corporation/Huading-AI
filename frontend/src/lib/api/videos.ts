import { API_BASE_URL, ApiError, apiFetch, authHeaders } from "@/lib/api/client";
import { authStore } from "@/lib/auth/store";
import type { ClearResult, CreateVideoRequest, DeleteResult, EstimateResponse, ScenePromptResponse, VideoAccepted, VideoDetail, VideoEvent, VideoListItem, VideoListResponse } from "@/lib/api/types";

export function createVideo(input: CreateVideoRequest): Promise<VideoAccepted> {
  return apiFetch<VideoAccepted>("/api/v1/videos", { method: "POST", body: input });
}

/** Estimate the credits a request would consume — shown in the 确定生成 dialog. */
export function estimateVideo(input: CreateVideoRequest): Promise<EstimateResponse> {
  return apiFetch<EstimateResponse>("/api/v1/videos/estimate", { method: "POST", body: input });
}

/** Generate a 画面提示词 (scene prompt) for 电商带货 i2v from the topic — decoupled
 *  from the 口播 script so editing one never changes the other. */
export function generateScenePrompt(topic: string): Promise<ScenePromptResponse> {
  return apiFetch<ScenePromptResponse>("/api/v1/videos/scene-prompt", { method: "POST", body: { topic } });
}

/** Authoritative record for one video (status + playback/download URLs). */
export function getVideo(id: string): Promise<VideoDetail> {
  return apiFetch<VideoDetail>(`/api/v1/videos/${encodeURIComponent(id)}`, { method: "GET" });
}

/** This tenant's videos, newest first — used to hydrate the task list on mount. */
export async function listVideos(): Promise<VideoListItem[]> {
  const res = await apiFetch<VideoListResponse>("/api/v1/videos", { method: "GET" });
  return res?.items ?? [];
}

/** One page of videos, optionally filtered by mode — backs the 历史生成 tabs
 *  (returns total so the caller can paginate via offset). */
export function listVideosPage(
  params: { mode?: string; kind?: string; limit?: number; offset?: number } = {}
): Promise<VideoListResponse> {
  const query = new URLSearchParams();
  if (params.mode) query.set("mode", params.mode);
  if (params.kind) query.set("kind", params.kind); // 图片细分筛（kind=cover 仅封面，真后端）
  if (params.limit != null) query.set("limit", String(params.limit));
  if (params.offset != null) query.set("offset", String(params.offset));
  const qs = query.toString();
  return apiFetch<VideoListResponse>(`/api/v1/videos${qs ? `?${qs}` : ""}`, { method: "GET" });
}

/** 硬删单条视频/图片(+媒体 best-effort)；跨租户/不存在 → 404(HIST-UI-0001)。 */
export function deleteVideo(id: string): Promise<DeleteResult> {
  return apiFetch<DeleteResult>(`/api/v1/videos/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** 清空某模块全部视频/图片(mode 必填，硬删 + 媒体 best-effort)。 */
export function clearVideos(mode: string): Promise<ClearResult> {
  return apiFetch<ClearResult>(`/api/v1/videos?mode=${encodeURIComponent(mode)}`, { method: "DELETE" });
}

/**
 * Subscribe to the SSE progress stream. EventSource can't send Authorization /
 * X-Tenant-ID headers, so we read the stream over fetch and parse `data:` lines
 * ourselves. Resolves when the stream ends; pass an AbortSignal to cancel.
 */
export async function streamVideoEvents(
  taskId: string,
  onMessage: (event: VideoEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/videos/${encodeURIComponent(taskId)}/events`,
    { headers: { Accept: "text/event-stream", ...authHeaders() }, signal }
  );

  if (res.status === 401) {
    if (authStore.get()) authStore.clear();
    throw new ApiError("登录已过期，请重新登录。", "UNAUTHORIZED", 401);
  }
  if (!res.ok || !res.body) {
    throw new ApiError(`进度订阅失败（${res.status}）`, "SSE_ERROR", res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const dataLine = frame.split("\n").find((line) => line.startsWith("data:"));
      if (!dataLine) continue;
      try {
        onMessage(JSON.parse(dataLine.slice(5).trim()) as VideoEvent);
      } catch {
        // ignore malformed frame
      }
    }
  }
}
