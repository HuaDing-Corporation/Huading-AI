"use client";

import {
  AlertTriangle,
  Ban,
  Check,
  Clapperboard,
  Clock,
  Download,
  Play,
  Trash2,
  type LucideIcon
} from "lucide-react";

import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/ui/status-badge";
import { AiLabelNotice } from "@/components/label/ai-label-notice";
import { friendlyImageError } from "@/lib/api/image-error";
import { friendlyVideoError } from "@/lib/api/video-error";
import { useMediaUrlRefresh } from "@/lib/media/use-media-url-refresh";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import type { TrackedTask, UiStatus } from "@/lib/sse/progress-mapping";

// ── Thumbnail icon set ────────────────────────────────────────────────────────

const thumbIcon: Record<UiStatus, LucideIcon> = {
  running: Clapperboard,
  done: Check,
  queued: Clock,
  failed: AlertTriangle,
  cancelled: Ban
};

const thumbStyle: Record<UiStatus, string> = {
  running: "bg-grad-gold text-ink shadow-thumb",
  done: "bg-grad-done text-ink shadow-thumb-done",
  queued: "bg-track text-ink-faint",
  failed: "bg-error-bg text-error-fg shadow-thumb-failed",
  cancelled: "bg-track text-ink-faint"
};

// ── Props ─────────────────────────────────────────────────────────────────────

export interface TaskCardProps {
  task: TrackedTask;
  /**
   * 「查看详情」的落点。HISTORY-VIDEO-DIALOG-UI-0001：本 prop 的**契约未变**（点「查看详情」→ 以 taskId 调用），
   * 变的是各调用方怎么接它 —— 历史列表现在接到视频详情弹窗（弹窗内再提供「打开详情页」），工作台 TaskList
   * 仍接 router.push。故 TaskCard 的既有测试（含补网那批）原样成立。
   */
  onOpen: (id: string) => void;
  /**
   * 「播放视频」→ 大屏 overlay（HISTORY-VIDEO-DIALOG-UI-0001）。**可选**：只有历史列表传，工作台 TaskList
   * 不传 → 不渲染该入口，零回归。仅视频（非 photo）且已有播放地址时才显示。
   */
  onOpenMedia?: () => void;
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
export function TaskCard({ task, onOpen, onOpenMedia, onRetry, onUrlError, onDelete, deleting }: TaskCardProps) {
  // 兜底 ?? Clock：即便后端未来再冒未知状态（前端类型未及时补），也走「排队」图标而非 undefined → 不 #130 白屏。
  const Icon = thumbIcon[task.status] ?? Clock;
  const showPlayer = task.status === "done" && !!task.playbackUrl;
  const isImage = task.mode === "photo";
  // 失败均映射友好中文（不露裸 error_message/技术串）：photo→friendlyImageError，视频→friendlyVideoError（VIDEO-ERR-MAP-UI）。
  const failureText = isImage ? friendlyImageError(task.errorCode) : friendlyVideoError(task.errorCode);

  // presign 失效 → 重取一次。**改用共享哨兵**（HISTORY-VIDEO-DIALOG-UI-0001 · FIX1）。
  //
  // 这里原本是本仓第 2 份手抄实现，注释写着 "mirrors VideoPlayer" —— 但实测它并没有 mirror：
  // 它比 VideoPlayer 多一个「URL 变更重置」，行为其实**不一样**（而且更好）。三处「拷贝」三个行为，
  // 没有任何测试拦得住这种漂移，因为每处各测各的。收敛成一份的理由不是「少写几行」，是**拷贝会漂移**。
  //
  // 迁移后行为变化只有一个：连续失败封顶（本处原先没有 → 对象被删时会 error→refetch→新 URL→error…… 无限打后端）。
  // 既有承重（task-card.test.tsx:38「fires onUrlError at most once across repeated errors」）原样全绿 = 零回归证据。
  const media = useMediaUrlRefresh(task.playbackUrl, () => onUrlError(task.taskId));

  return (
    <div className="border-b border-track py-3.5 last:border-none">
      {/* Header row: icon · title/duration · status badge */}
      <div className="flex items-center gap-3.5 px-2">
        <div
          className={cn(
            "flex h-12 w-12 flex-none items-center justify-center rounded-chip",
            thumbStyle[task.status] ?? "bg-track text-ink-faint"
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
          {/* LABEL-TOGGLE-UI-0001：按任务实际状态显示徽标；带=显示，不带=不显示。 */}
          {task.status === "done" && task.applyVisibleLabel ? (
            <div className="mt-1">
              <AiLabelNotice />
            </div>
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
            <p className="mb-1.5 text-[12px] text-error-fg" title={failureText ?? undefined}>
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
              onError={media.onError}
              onLoad={media.onLoad}
              className="max-h-[320px] w-full rounded-field border border-line-gold bg-black/5 object-contain"
            />
          ) : (
            <video
              controls
              preload="metadata"
              poster={task.thumbnailUrl ?? undefined}
              src={task.playbackUrl ?? undefined}
              onError={media.onError}
              onLoadedMetadata={media.onLoad}
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
            {/* 「播放视频」→ 大屏 overlay（点内容 → 大图/播放，与图片 tab 同款交互语言）。仅历史列表传
                onOpenMedia；图片结果不给（TaskCard 的 photo 分支只在工作台 TaskList 用，图片历史走 HistoryGrid）。 */}
            {onOpenMedia && !isImage && (
              <button
                type="button"
                onClick={onOpenMedia}
                className="inline-flex items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep transition-colors hover:bg-glass-hover"
              >
                <Play size={14} strokeWidth={2} /> {copy.history.videoPlay}
              </button>
            )}
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
