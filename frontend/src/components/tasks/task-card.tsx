"use client";

import {
  AlertTriangle,
  Check,
  Clapperboard,
  Clock,
  Download,
  type LucideIcon
} from "lucide-react";

import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/ui/status-badge";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import type { TrackedTask, UiStatus } from "@/lib/sse/progress-mapping";

// ── Thumbnail icon set ────────────────────────────────────────────────────────

const thumbIcon: Record<UiStatus, LucideIcon> = {
  running: Clapperboard,
  done: Check,
  queued: Clock,
  failed: AlertTriangle
};

const thumbStyle: Record<UiStatus, string> = {
  running: "bg-grad-gold text-ink shadow-thumb",
  done: "bg-grad-done text-ink shadow-thumb-done",
  queued: "bg-track text-ink-faint",
  failed: "bg-error-bg text-error-fg"
};

// ── Props ─────────────────────────────────────────────────────────────────────

export interface TaskCardProps {
  task: TrackedTask;
  /** Navigate to the video detail page. */
  onOpen: (id: string) => void;
  /** Re-submit the task (stored request keyed by taskId). */
  onRetry: (id: string) => void;
  /** Presigned URL has expired — refresh from the server. */
  onUrlError: (id: string) => void;
}

// ── Pure presentational TaskCard ─────────────────────────────────────────────

/**
 * Pure-props card for a tracked video task.  No hooks, no fetch.
 *
 * States:
 *  - queued   → slim progress bar (indeterminate-looking at 0%)
 *  - running  → progress bar + step label (already in task.statusLabel)
 *  - failed   → error message + retry button
 *  - done     → thumbnail + "open detail" button + inline video player
 */
export function TaskCard({ task, onOpen, onRetry, onUrlError }: TaskCardProps) {
  const Icon = thumbIcon[task.status];
  const showPlayer = task.status === "done" && !!task.playbackUrl;

  return (
    <div className="border-b border-track py-3.5 last:border-none">
      {/* Header row: icon · title/duration · status badge */}
      <div className="flex items-center gap-3.5 px-2">
        <div
          className={cn(
            "flex h-12 w-12 flex-none items-center justify-center rounded-chip",
            thumbStyle[task.status]
          )}
        >
          <Icon size={20} strokeWidth={1.8} />
        </div>

        <div className="min-w-0 flex-1">
          <b className="block truncate text-sm font-medium text-ink">{task.topic}</b>
          {task.status === "done" && task.durationSec ? (
            <span className="text-[12px] text-ink-faint">
              时长 {Math.round(task.durationSec)} 秒
            </span>
          ) : null}
          {task.status === "running" ? (
            <span className="mt-0.5 block text-[12px] text-ink-soft">{task.statusLabel}</span>
          ) : null}
        </div>

        <StatusBadge status={task.status}>{task.statusLabel}</StatusBadge>
      </div>

      {/* Body: state-specific content */}
      {task.status === "failed" ? (
        <div className="mt-2 px-2">
          {task.error ? (
            <p className="mb-1.5 text-[12px] text-error-fg" title={task.error}>
              {task.error}
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => onRetry(task.taskId)}
            className="inline-flex items-center rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep transition-colors hover:bg-glass-hover"
          >
            {copy.tasks.retry}
          </button>
        </div>
      ) : showPlayer ? (
        /* Done + playback URL: thumbnail player + open-detail + download */
        <div className="mt-3 px-2">
          <video
            controls
            preload="metadata"
            poster={task.thumbnailUrl ?? undefined}
            src={task.playbackUrl ?? undefined}
            onError={() => onUrlError(task.taskId)}
            className="max-h-[320px] w-full rounded-field border border-line-gold bg-black/5"
          />
          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              onClick={() => onOpen(task.taskId)}
              className="inline-flex items-center rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep transition-colors hover:bg-glass-hover"
            >
              {copy.tasks.open}
            </button>
            {task.downloadUrl && (
              <a
                href={task.downloadUrl}
                download
                className="inline-flex items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep transition-colors hover:bg-glass-hover"
              >
                <Download size={14} strokeWidth={2} /> {copy.detail.download}
              </a>
            )}
          </div>
        </div>
      ) : task.status === "done" ? (
        /* Done but no playback URL yet (still reconciling) */
        <div className="mt-2 px-2">
          <button
            type="button"
            onClick={() => onOpen(task.taskId)}
            className="inline-flex items-center rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep transition-colors hover:bg-glass-hover"
          >
            {copy.tasks.open}
          </button>
        </div>
      ) : (
        /* queued / running → progress bar */
        <Progress value={task.progress} className="mt-2.5 h-[5px]" />
      )}
    </div>
  );
}
