"use client";

import { Download, ExternalLink } from "lucide-react";

import { HistoryDetailDialog } from "@/components/history/history-detail-dialog";
import { AiLabelNotice } from "@/components/label/ai-label-notice";
import { Button } from "@/components/ui/button";
import { copy } from "@/lib/copy";
import { useMediaUrlRefresh } from "@/lib/media/use-media-url-refresh";
import type { TrackedTask } from "@/lib/sse/progress-mapping";

const formatCreatedAt = (iso: string) => (iso.includes("T") ? iso.replace("T", " ").slice(0, 16) : iso);

export interface VideoDetailPayload {
  task: TrackedTask;
  /**
   * 生成时间 —— 从列表项 `VideoListItem.created_at` 带入。TrackedTask 没有该字段，而 progress-mapping 是
   * **SSE 与列表共用**的映射；为一个展示项去动 SSE 链路，风险与收益不成比例 → 由调用方带入（与图片详情弹窗
   * 从列表项带 title 同一模式）。
   */
  createdAt: string;
  /** 模式中文（数字人视频历史 / 电商视频历史 / 视频生成历史）—— 对应图片详情弹窗的「分类」项。 */
  modeLabel: string;
}

/**
 * 视频详情弹窗（HISTORY-VIDEO-DIALOG-UI-0001）——「查看详情」的落点，与图片 tab 同款交互语言。
 *
 * **信息并集（一项都不能少）**：卡片现有的 topic / 状态 / 时长 / AI 标识徽标 / 播放 / 下载，
 * 并入图片详情弹窗那套的对应项：生成时间（卡片没显）、「分类」→ 这里是**模式**。
 *
 * 🔴 **跳 /videos/{id} 零回归**：升级前「查看详情」= 直接跳详情页；升级后按统一交互语言改为开本弹窗，
 * 故弹窗内保留「打开详情页」入口 → 交互语言统一 + 跳转能力一个不丢（TaskCard 的 onOpen 契约未变，
 * 变的只是调用方 HistoryList 把它接到弹窗而非 router.push）。
 */
export function VideoDetailDialog({
  detail,
  onClose,
  onOpenPage,
  onUrlError
}: {
  detail: VideoDetailPayload | null;
  onClose: () => void;
  onOpenPage: (taskId: string) => void;
  /** presign 失效 → 请调用方重取（通常是 `query.refetch()`）。哨兵与封顶在 useMediaUrlRefresh 里。 */
  onUrlError?: () => void;
}) {
  const task = detail?.task;
  const isImage = task?.mode === "photo";
  // 🔴 FIX1：图片/视频共用同一条防线 —— 本弹窗两种媒体都可能是 presign（isImage 时渲染 <img>）。
  const media = useMediaUrlRefresh(task?.playbackUrl, () => onUrlError?.());
  return (
    <HistoryDetailDialog
      open={detail !== null}
      onClose={onClose}
      title={task?.topic ?? copy.history.videoDetailTitle}
      titleAttr={task?.topic}
      meta={
        detail && task ? (
          <>
            <span>
              {copy.historyImages.detailCreatedLabel}：
              <span className="tabular-nums">{formatCreatedAt(detail.createdAt)}</span>
            </span>
            <span>
              {copy.historyImages.detailStatusLabel}：{task.statusLabel}
            </span>
            <span>
              {copy.historyImages.detailCategoryLabel}：{detail.modeLabel}
            </span>
            {task.durationSec ? (
              <span className="tabular-nums">时长 {Math.round(task.durationSec)} 秒</span>
            ) : null}
          </>
        ) : null
      }
    >
      {task ? (
        <div className="mt-3 flex flex-col gap-3">
          {task.playbackUrl ? (
            isImage ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={task.playbackUrl}
                alt={task.topic}
                onError={media.onError}
                onLoad={media.onLoad}
                className="max-h-[52vh] w-full rounded-field border border-line-gold object-contain"
              />
            ) : (
              // 详情弹窗内的播放器同样不 autoplay（与 VideoLightbox 同口径：有声内容由用户发起）。
              // 🔴 playsInline（FIX1）：否则 iPhone Safari 点播放即切系统全屏，用户被踢出这个弹窗 ——
              // 详情弹窗的信息并集（时长/分类/AI 标识/下载/打开详情页）全被系统播放器盖住，等于没做。
              <video
                controls
                playsInline
                preload="metadata"
                poster={task.thumbnailUrl ?? undefined}
                src={task.playbackUrl}
                aria-label={task.topic}
                onError={media.onError}
                onLoadedMetadata={media.onLoad}
                className="max-h-[52vh] w-full rounded-field border border-line-gold bg-black"
              />
            )
          ) : (
            // done 但尚无播放地址（仍在对账）/ 未完成 → 明确说明，不给空白。
            <p className="py-6 text-center text-[13px] text-ink-soft">{copy.history.videoNoPlayback}</p>
          )}

          {/* LABEL-TOGGLE-UI-0001：按任务实际状态显示徽标（并集自卡片）。 */}
          {task.status === "done" && task.applyVisibleLabel ? <AiLabelNotice /> : null}

          <div className="flex flex-wrap items-center gap-2">
            {/* 🔴 跳转能力零回归：升级前卡片「查看详情」直接跳这里，现在入口移进弹窗。 */}
            <Button variant="soft" size="sm" onClick={() => onOpenPage(task.taskId)}>
              <ExternalLink size={14} strokeWidth={2} /> {copy.history.videoOpenPage}
            </Button>
            {task.downloadUrl ? (
              <a
                href={task.downloadUrl}
                download
                className="inline-flex items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep transition-colors hover:bg-glass-hover"
              >
                <Download size={14} strokeWidth={2} /> {isImage ? copy.detail.downloadImage : copy.detail.download}
              </a>
            ) : null}
          </div>
        </div>
      ) : null}
    </HistoryDetailDialog>
  );
}
