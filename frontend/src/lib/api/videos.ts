import { API_BASE_URL, ApiError, apiFetch, authHeaders } from "@/lib/api/client";
import { authStore } from "@/lib/auth/store";
import type { CreateVideoRequest, VideoAccepted, VideoTaskStatus } from "@/lib/api/types";

export function createVideo(input: CreateVideoRequest): Promise<VideoAccepted> {
  return apiFetch<VideoAccepted>("/api/v1/videos", { method: "POST", body: input });
}

export function getVideoStatus(taskId: string): Promise<VideoTaskStatus> {
  return apiFetch<VideoTaskStatus>(`/api/v1/videos/${encodeURIComponent(taskId)}`, {
    method: "GET"
  });
}

/**
 * Subscribe to the SSE progress stream. EventSource can't send Authorization /
 * X-Tenant-ID headers, so we read the stream over fetch and parse `data:` lines
 * ourselves. Resolves when the stream ends; pass an AbortSignal to cancel.
 */
export async function streamVideoEvents(
  taskId: string,
  onMessage: (event: VideoTaskStatus) => void,
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
      const dataLine = frame
        .split("\n")
        .find((line) => line.startsWith("data:"));
      if (!dataLine) continue;
      try {
        onMessage(JSON.parse(dataLine.slice(5).trim()) as VideoTaskStatus);
      } catch {
        // ignore malformed frame
      }
    }
  }
}
