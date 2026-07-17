"use client";

import { useCallback, useState } from "react";
import { ChevronLeft, Download, Image as ImageIcon, Share2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { ApiError } from "@/lib/api/client";
import { useVideo } from "@/lib/api/hooks";
import { videoKeys } from "@/lib/api/keys";
import { friendlyImageError } from "@/lib/api/image-error";
import { friendlyVideoError } from "@/lib/api/video-error";
import { copy } from "@/lib/copy";
import { useMediaUrlRefresh } from "@/lib/media/use-media-url-refresh";
import { cn } from "@/lib/utils";
import { useQueryClient } from "@tanstack/react-query";

import { SubtitlePreview } from "@/components/video/subtitle-preview";
import { VideoPlayer } from "@/components/video/video-player";
import { CoverPanel } from "@/components/video/cover-panel";
import { AiLabelNotice } from "@/components/label/ai-label-notice";
import { Button } from "@/components/ui/button";

export interface VideoDetailProps {
  id: string;
}

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    });
  } catch {
    return iso;
  }
}

function formatDuration(ms: number | null | undefined) {
  if (ms == null) return null;
  const s = Math.round(ms / 1000);
  return `${s} 秒`;
}

/**
 * Container component for the video detail page.
 * Calls useVideo(id) and handles:
 *  - loading state
 *  - 404 / not_found → dedicated empty state (nf-1)
 *  - other errors → error message
 *  - success → VideoPlayer + SubtitlePreview + meta
 */
export function VideoDetail({ id }: VideoDetailProps) {
  const { data, error, isLoading } = useVideo(id);
  const queryClient = useQueryClient();
  const router = useRouter();
  const [coverOpen, setCoverOpen] = useState(false);

  /** presign 失效 → 重取本视频详情。两个分支（photo 的 `<img>` / video 的 VideoPlayer）共用这一条动作。 */
  const handleUrlExpired = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: videoKeys.detail(id) });
  }, [queryClient, id]);

  /**
   * photo 分支的 `<img>` 迁入**共享哨兵**（MEDIA-URL-REFRESH-CONVERGE-0001）—— 它此前是**裸接**的：
   * 没有任何哨兵，只要 BE 每次都能签出**新的**失效 URL（对象已删/已迁移即如此），就是
   * error → invalidate → 新 URL → error → …… **无限重取**，每轮真打一次后端。
   *
   * hook 必须在早返回（loading / error / !data）**之前**调用 —— rules-of-hooks，且 lint 是 error 级。
   * 故这里传 `data?.playback_url`：无数据时是 undefined，hook 的 `if (!url) return` 让它自然哑火。
   * video 分支的哨兵在 VideoPlayer 内部（同一个 hook），两分支同一时刻只渲染一个，互不干扰。
   */
  const media = useMediaUrlRefresh(data?.playback_url, handleUrlExpired);

  // Loading state
  if (isLoading) {
    return (
      <div className="flex min-h-[240px] items-center justify-center">
        <span className="text-[14px] text-ink-soft">加载中…</span>
      </div>
    );
  }

  // nf-1: 404 / not_found → dedicated empty state, NOT a generic toast
  if (error instanceof ApiError && (error.status === 404 || error.code === "not_found")) {
    return (
      <div className="flex min-h-[320px] flex-col items-center justify-center gap-4 text-center">
        <p className="text-[15px] text-ink-soft">{copy.detail.notFound}</p>
        <button
          type="button"
          onClick={() => router.back()}
          className="inline-flex items-center gap-1 rounded-field border border-line-gold bg-glass-fill px-4 py-2 text-[13px] text-gold-deep transition-colors hover:bg-glass-hover"
        >
          <ChevronLeft size={15} strokeWidth={2} />
          {copy.detail.back}
        </button>
      </div>
    );
  }

  // Other errors
  if (error) {
    return (
      <div className="flex min-h-[240px] flex-col items-center justify-center gap-3 text-center">
        <p className="text-[14px] text-error-fg">
          {error instanceof ApiError ? error.message : copy.errors.generic}
        </p>
        <button
          type="button"
          onClick={() => router.back()}
          className="inline-flex items-center gap-1 rounded-field border border-line-gold bg-glass-fill px-4 py-2 text-[13px] text-gold-deep transition-colors hover:bg-glass-hover"
        >
          <ChevronLeft size={15} strokeWidth={2} />
          {copy.detail.back}
        </button>
      </div>
    );
  }

  if (!data) return null;

  const statusLabel: Record<string, string> = {
    queued: copy.status.queued,
    running: `生成中 ${data.progress}%`,
    done: copy.status.done,
    failed: copy.status.failed,
    cancelled: copy.status.cancelled // 批量退分产生的 cancelled 也显中文「已取消」，不露英文（ECOM-HISTORY-CANCELLED-FIX-0001）
  };

  return (
    <div className="flex flex-col gap-6">
      {/* Back navigation */}
      <nav aria-label="导航">
        <button
          type="button"
          onClick={() => router.back()}
          className="inline-flex items-center gap-1 rounded-field px-2 py-1 text-[13px] text-gold-deep outline-none transition-colors hover:bg-glass-soft focus-visible:shadow-focus-gold"
        >
          <ChevronLeft size={15} strokeWidth={2} />
          {copy.detail.back}
        </button>
      </nav>

      {/* Topic heading */}
      <header>
        <h1 className="text-[22px] font-semibold tracking-wide text-ink">{data.topic ?? "未命名视频"}</h1>
      </header>

      {/* Player — only when done and URL is available */}
      {data.status === "done" && data.playback_url ? (
        data.mode === "photo" ? (
          <div className="flex flex-col gap-3">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={data.playback_url}
              alt={data.topic ?? copy.workbench.photoResultAlt}
              onError={media.onError}
              onLoad={media.onLoad}
              className="w-full rounded-field border border-line-gold bg-black/5 object-contain"
            />
            {data.download_url && (
              <a
                href={data.download_url}
                download
                className="inline-flex w-fit items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-4 py-2 text-[13px] text-gold-deep transition-colors hover:bg-glass-hover"
              >
                <Download size={15} strokeWidth={2} /> {copy.detail.downloadImage}
              </a>
            )}
          </div>
        ) : (
          <VideoPlayer
            playbackUrl={data.playback_url}
            downloadUrl={data.download_url}
            poster={data.thumbnail_url}
            onUrlExpired={handleUrlExpired}
          />
        )
      ) : (
        <div className="flex min-h-[160px] items-center justify-center rounded-field border border-line-gold bg-glass-fill">
          <span className="text-[13px] text-ink-soft">{statusLabel[data.status] ?? data.status}</span>
        </div>
      )}

      {/* 发布入口 + 标识徽标：仅完成产物显示。徽标按任务实际 apply_visible_label(LABEL-TOGGLE-UI-0001)；发布入口不受影响。 */}
      {data.status === "done" && data.playback_url && (
        <div className="flex flex-wrap items-center gap-2">
          {data.apply_visible_label && <AiLabelNotice className="w-fit" />}
          {/* 发布入口：带 source_kind+source_task_id 跳发布中心(不在此发布，仅引导)。 */}
          <Link
            href={`/publish?source_kind=${data.mode === "photo" ? "image" : "video"}&source_task_id=${id}`}
            className="inline-flex items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1 text-[12px] text-gold-deep transition-colors hover:bg-glass-hover"
          >
            <Share2 size={13} strokeWidth={2} /> {copy.publish.entry}
          </Link>
        </div>
      )}

      {/* 做封面(ORAL-PROD-UI-0001)：封面是口播视频产物附属，仅完成的口播视频显示入口 */}
      {data.status === "done" && data.playback_url && (data.mode == null || data.mode === "avatar_talk") && (
        <div>
          <Button variant="soft" size="sm" onClick={() => setCoverOpen(true)}>
            <ImageIcon size={15} strokeWidth={1.8} /> {copy.cover.entry}
          </Button>
          <CoverPanel videoTaskId={id} open={coverOpen} onOpenChange={setCoverOpen} />
        </div>
      )}

      {/* Script / subtitle preview */}
      {data.script && <SubtitlePreview script={data.script} />}

      {/* Meta info */}
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-[13px] sm:grid-cols-3">
        <div>
          <dt className="text-ink-faint">状态</dt>
          <dd className={cn("font-medium", data.status === "failed" ? "text-error-fg" : "text-ink")}>
            {statusLabel[data.status] ?? data.status}
          </dd>
        </div>
        {data.duration_ms != null && (
          <div>
            <dt className="text-ink-faint">时长</dt>
            <dd className="text-ink">{formatDuration(data.duration_ms)}</dd>
          </div>
        )}
        <div>
          <dt className="text-ink-faint">创建时间</dt>
          <dd className="text-ink">{formatDate(data.created_at)}</dd>
        </div>
        {data.status === "failed" && (
          <div className="col-span-full">
            <dt className="text-ink-faint">错误信息</dt>
            {/* 失败均映射友好中文（不露裸 error_message）：photo→friendlyImageError，视频→friendlyVideoError（VIDEO-ERR-MAP-UI）。 */}
            <dd className="text-error-fg">
              {data.mode === "photo" ? friendlyImageError(data.error_code) : friendlyVideoError(data.error_code)}
            </dd>
          </div>
        )}
      </dl>
    </div>
  );
}
