"use client";

import { useState } from "react";
import { ImageOff, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { HistoryCard } from "@/components/history/history-card";
import { HistorySetDialog } from "@/components/history/history-set-dialog";
import { ImageLightbox } from "@/components/history/image-lightbox";
import { useDeleteHistoryImage, useHistoryImages } from "@/lib/api/hooks";
import { copy } from "@/lib/copy";
import type { HistoryCategory, HistoryItem } from "@/lib/api/history-images";

/**
 * 单分类历史网格（HISTORY-IMAGE-TAB-UI-0001）——按 category 分页拉列表（category 省略 = 全部图片），
 * 卡片网格 + 加载/错误/空态 + 「加载更多」。交互三分：点图 → 大图弹窗；「查看详情」→ 详情弹窗（整套 + 信息并集）；
 * 删除 → 硬删确认（deleteConfirmHard，防连点）。每个分类各自 useHistoryImages(category)（按 key 缓存）→ 切分类不串数据。
 */
export function HistoryGrid({ category }: { category?: HistoryCategory }) {
  const query = useHistoryImages(category);
  const del = useDeleteHistoryImage();
  const [lightbox, setLightbox] = useState<HistoryItem | null>(null);
  const [detail, setDetail] = useState<HistoryItem | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<HistoryItem | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];

  const onConfirmDelete = async () => {
    if (!confirmDelete) return;
    setActionError(null);
    try {
      await del.mutateAsync({ category: confirmDelete.category, id: confirmDelete.id });
      setConfirmDelete(null);
    } catch {
      setActionError(copy.history.deleteFailed);
    }
  };

  if (query.isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 py-12 text-[13px] text-ink-soft" role="status" aria-live="polite">
        <Loader2 size={18} className="animate-spin" /> {copy.historyImages.loading}
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="flex flex-col items-center gap-3 py-12 text-center">
        <p role="alert" className="text-[13px] text-error-fg">
          {copy.historyImages.error}
        </p>
        <Button variant="soft" size="sm" onClick={() => void query.refetch()}>
          {copy.historyImages.retry}
        </Button>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-14 text-center text-ink-soft" role="status" aria-live="polite">
        <ImageOff size={30} strokeWidth={1.6} className="text-ink-faint" />
        <p className="text-[13.5px] text-ink">{copy.historyImages.empty}</p>
        <p className="text-[12px] text-ink-faint">{copy.historyImages.emptyHint}</p>
      </div>
    );
  }

  return (
    <>
      {actionError && (
        <p role="alert" className="mb-2 rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
          {actionError}
        </p>
      )}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {items.map((item) => (
          <HistoryCard
            key={item.id}
            item={item}
            onOpenImage={() => setLightbox(item)}
            onDetail={() => setDetail(item)}
            onDelete={() => setConfirmDelete(item)}
            deleting={del.isPending && del.variables?.id === item.id}
          />
        ))}
      </div>
      {query.hasNextPage ? (
        <div className="mt-4 flex justify-center">
          <Button variant="soft" size="sm" onClick={() => void query.fetchNextPage()} disabled={query.isFetchingNextPage}>
            {query.isFetchingNextPage ? copy.historyImages.loading : copy.historyImages.loadMore}
          </Button>
        </div>
      ) : null}

      <ImageLightbox
        open={lightbox !== null}
        src={lightbox?.cover_url ?? null}
        alt={lightbox ? copy.historyImages.lightboxAlt(lightbox.title) : ""}
        onClose={() => setLightbox(null)}
      />
      <HistorySetDialog item={detail} onClose={() => setDetail(null)} />
      <ConfirmDialog
        open={confirmDelete !== null}
        title={copy.history.deleteConfirmTitle}
        message={copy.history.deleteConfirmHard}
        confirmLabel={copy.history.deleteConfirmBtn}
        danger
        submitting={del.isPending}
        error={actionError}
        onConfirm={() => void onConfirmDelete()}
        onCancel={() => {
          setConfirmDelete(null);
          setActionError(null);
        }}
      />
    </>
  );
}
