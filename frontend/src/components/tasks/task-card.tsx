"use client";

import { useEffect, useRef } from "react";
import {
  AlertTriangle,
  Check,
  Clapperboard,
  Clock,
  Download,
  Trash2,
  type LucideIcon
} from "lucide-react";

import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/ui/status-badge";
import { friendlyImageError } from "@/lib/api/image-error";
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
  failed: "bg-error-bg text-error-fg shadow-thumb-failed"
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
  /** 历史项删除(trash)；仅历史列表传入，live 任务列表不传(不显删除)。HIST-UI-0001。 */
  onDelete?: (id: string) => void;
  /** 删除请求中(防连点)。 */
  deleting?: boolean;
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
export function TaskCard({ task, onOpen, onRetry, onUrlError, onDelete, deleting }: TaskCardProps) {
  const Icon = thumbIcon[task.status];
  const showPlayer = task.status === "done" && !!task.playbackUrl;
  const isImage = task.mode === "photo";
  // photo 失败映射友好文案（不露原始 JSON）；视频沿用原始 message（不破）。
  const failureText = isImage ? friendlyImageError(task.errorCode) : task.error;

  // Fire onUrlError at most once per playback URL (mirrors VideoPlayer); reset
  // the guard when the URL changes so a refreshed URL can error once again (P2-2).
  const urlErrored = useRef(false);
  useEffect(() => {
    urlErrored.current = false;
  }, [task.playbackUrl]);
  const handleVideoError = () => {
    if (urlErrored.current) return;
    urlErrored.current = true;
    onUrlError(task.taskId);
  };

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
        {onDelete && (
          <button
            type="button"
            onClick={() => onDelete(task.taskId)}
            disabled={deleting}
            aria-label={copy.history.deleteItem}
            title={copy.history.deleteItem}
            className="flex h-8 w-8 flex-none items-center justify-center rounded-field text-ink-faint transition-colors hover:bg-error-bg hover:text-error-fg focus-visible:shadow-focus-gold disabled:pointer-events-none disabled:opacity-50"
          >
            <Trash2 size={15} strokeWidth={1.8} />
          </button>
        )}
      </div>

      {/* Body: state-specific content */}
      {task.status === "failed" ? (
        <div className="mt-2 px-2">
          {failureText ? (
            <p
              className="mb-1.5 text-[12px] text-error-fg"
              title={isImage ? undefined : task.error ?? undefined}
            >
              {failureText}
            </p>
          ) : null}
          {/* Only offer retry when we hold the original request (this-session
              tasks). Hydrated-from-list failed tasks have no stored request, so
              show a "refill on the workbench" hint instead of silently failing (P2-1). */}
          {task.retryable ? (
            <button
              type="button"
              onClick={() => onRetry(task.taskId)}
              className="inline-flex items-center rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep transition-colors hover:bg-glass-hover"
            >
              {copy.tasks.retry}
            </button>
          ) : (
            <p className="text-[12px] text-ink-faint">{copy.tasks.retryUnavailable}</p>
          )}
        </div>
      ) : showPlayer ? (
        /* Done + playback URL: thumbnail player + open-detail + download */
        <div className="mt-3 px-2">
          {isImage ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={task.playbackUrl ?? undefined}
              alt={task.topic}
              loading="lazy"
              onError={handleVideoError}
              className="max-h-[320px] w-full rounded-field border border-line-gold bg-black/5 object-contain"
            />
          ) : (
            <video
              controls
              preload="metadata"
              poster={task.thumbnailUrl ?? undefined}
              src={task.playbackUrl ?? undefined}
              onError={handleVideoError}
              className="max-h-[320px] w-full rounded-field border border-line-gold bg-black/5"
            />
          )}
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
                <Download size={14} strokeWidth={2} /> {isImage ? copy.detail.downloadImage : copy.detail.download}
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
