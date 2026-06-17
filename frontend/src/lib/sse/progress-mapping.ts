import type { VideoRead, VideoStatus } from "@/lib/api/types";

export type UiStatus = VideoStatus; // queued | running | done | failed

export interface TrackedTask {
  taskId: string;
  topic: string;
  status: UiStatus;
  progress: number; // 0..100
  statusLabel: string;
  playbackUrl?: string | null;
  downloadUrl?: string | null;
  thumbnailUrl?: string | null;
  durationSec?: number | null;
  error?: string | null;
}

export const TERMINAL: UiStatus[] = ["done", "failed"];

export function labelFor(status: UiStatus, pct: number): string {
  switch (status) {
    case "done":
      return "已完成";
    case "failed":
      return "失败";
    case "running":
      return `生成中 ${pct}%`;
    default:
      return "排队中";
  }
}

/** SSE frame -> UI status (the stream still uses uppercase worker statuses). */
export function mapSseStatus(status: string | undefined): UiStatus {
  switch ((status ?? "").toUpperCase()) {
    case "SUCCESS":
    case "DONE":
      return "done";
    case "FAILURE":
    case "FAILED":
      return "failed";
    case "PROGRESS":
    case "STARTED":
    case "RUNNING":
      return "running";
    default:
      return "queued";
  }
}

export function fromVideoRead(read: VideoRead): TrackedTask {
  const pct = read.progress ?? 0;
  return {
    taskId: read.id,
    topic: read.title || read.prompt || "未命名视频",
    status: read.status,
    progress: pct,
    statusLabel: labelFor(read.status, pct),
    playbackUrl: read.playback_url ?? null,
    downloadUrl: read.download_url ?? null,
    thumbnailUrl: read.thumbnail_url ?? null,
    durationSec: read.duration_sec ?? null,
    error: read.error ?? null
  };
}
