import type { VideoDetail, VideoEvent, VideoListItem, VideoStatus } from "@/lib/api/types";

export type UiStatus = VideoStatus;

export interface TrackedTask {
  taskId: string;
  topic: string;
  status: UiStatus;
  progress: number;
  statusLabel: string;
  /** Generation mode — "photo" renders an <img> result; videos render <video>. */
  mode?: string | null;
  playbackUrl?: string | null;
  downloadUrl?: string | null;
  thumbnailUrl?: string | null;
  durationSec?: number | null;
  error?: string | null;
  /**
   * True only for tasks this session created via createAndTrack (we hold their
   * original request and can re-submit). Hydrated-from-list tasks omit it, so
   * the UI hides "retry" instead of calling retryTask and failing silently (P2-1).
   */
  retryable?: boolean;
}

export const TERMINAL: UiStatus[] = ["done", "failed"];

const STEP_LABEL: Record<string, string> = {
  script: "撰写文案",
  tts: "合成语音",
  avatar: "驱动形象",
  subtitle: "生成字幕",
  compose: "合成视频",
  upload: "上传成片"
};

export function labelFor(status: UiStatus, pct: number, step?: string | null): string {
  switch (status) {
    case "done":
      return "已完成";
    case "failed":
      return "失败";
    case "running":
      return step && STEP_LABEL[step] ? `${STEP_LABEL[step]} ${pct}%` : `生成中 ${pct}%`;
    default:
      return "排队中";
  }
}

const OLD_UPPER = /^[A-Z_]+$/;

export function mapSseStatus(status: string | undefined): UiStatus {
  switch ((status ?? "").toLowerCase()) {
    case "success":
    case "done":
      return "done";
    case "failure":
    case "failed":
      return "failed";
    case "progress":
    case "started":
    case "running":
      return "running";
    default:
      return "queued";
  }
}

export interface ProgressSnapshot {
  status: UiStatus;
  progress: number;
  statusLabel: string;
  error?: string | null;
}

export function progressFields(status: UiStatus, pct: number, step?: string | null): ProgressSnapshot {
  return { status, progress: status === "done" ? 100 : pct, statusLabel: labelFor(status, Math.round(pct), step) };
}

// New frame: progress is a 0..100 number (trust it). Old frame (UPPERCASE status):
// progress is 0..1 → rescale. Gate ONLY on the status casing, never the value,
// so a new {status:"running",progress:1} stays 1% (sse-1).
export function eventToProgress(event: VideoEvent): ProgressSnapshot | null {
  if (event.stage === "sse_timeout") return null;
  const raw = event.progress ?? 0;
  const isOld = typeof event.status === "string" && OLD_UPPER.test(event.status);
  const pct = isOld ? Math.round(raw * 100) : raw;
  const status = mapSseStatus(event.status);
  return { ...progressFields(status, pct, event.step), error: event.error_message ?? event.error ?? undefined };
}

export function fromVideoRead(read: VideoDetail | VideoListItem): TrackedTask {
  const pct = read.progress ?? 0;
  const detail = read as Partial<VideoDetail>;
  return {
    taskId: read.id,
    topic: read.topic || "未命名视频",
    status: read.status,
    progress: pct,
    statusLabel: labelFor(read.status, pct),
    mode: read.mode ?? null,
    playbackUrl: detail.playback_url ?? null,
    downloadUrl: detail.download_url ?? null,
    thumbnailUrl: read.thumbnail_url ?? null,
    durationSec: detail.duration_ms != null ? detail.duration_ms / 1000 : null,
    error: detail.error_message ?? null
  };
}
