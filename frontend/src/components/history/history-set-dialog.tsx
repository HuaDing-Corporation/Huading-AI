"use client";

import { Loader2, X } from "lucide-react";

import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { HistoryImageTile } from "@/components/history/history-image-tile";
import { HistoryStatusBadge } from "@/components/history/history-status-badge";
import { useHistoryImageSet } from "@/lib/api/hooks";
import { copy } from "@/lib/copy";
import type { HistoryCategory, HistoryItem } from "@/lib/api/history-images";

/** 分类机器键 → 中文标签（详情弹窗信息并集用；6 分类）。 */
const CATEGORY_LABEL: Record<HistoryCategory, string> = {
  image_gen: copy.historyImages.tabImageGen,
  ecom_white: copy.historyImages.tabEcomWhite,
  ecom_model: copy.historyImages.tabEcomModel,
  ecom_detail: copy.historyImages.tabEcomDetail,
  cover: copy.historyImages.catCover
};
const formatCreatedAt = (iso: string) => (iso.includes("T") ? iso.replace("T", " ").slice(0, 16) : iso);

/**
 * 详情弹窗（HISTORY-IMAGE-TAB-UI-0001，两边信息取并集）——点「查看详情」→ 拉 GET /history/images/{category}/{id}
 * → 完整展示该次整套（每张原图 download_url + 原始尺寸 + 缺失禁用，原图红线不变）。**并入**「历史生成」卡片
 * 现有信息：生成时间 / 状态徽标 / 分类 / 张数（这些原 HISTORY-UI 弹窗没显）。标题/created_at/status/category 从
 * **列表项 `item` 带入**（BE 详情响应 `ImageHistoryDetailResponse` 无 title，同反推 source_thumbnail_url 模式）。
 * item=null 即关闭（enabled 门控 → 不打开不发请求）。
 */
export function HistorySetDialog({ item, onClose }: { item: HistoryItem | null; onClose: () => void }) {
  const query = useHistoryImageSet(item?.category ?? "", item?.id);
  const set = query.data;
  const categoryLabel = item ? (CATEGORY_LABEL[item.category as HistoryCategory] ?? item.category) : "";
  return (
    <Dialog
      open={item !== null}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="w-[min(94vw,880px)] max-h-[85vh] overflow-y-auto">
        <div className="mb-1 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <DialogTitle className="truncate text-base font-semibold text-ink" title={item?.title}>
              {item?.title ?? copy.historyImages.setTitle}
            </DialogTitle>
            {/* 信息并集：生成时间 · 状态 · 分类 · 张数（卡片有、原弹窗没显）。 */}
            <DialogDescription className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-soft">
              {item ? (
                <>
                  <span>
                    {copy.historyImages.detailCreatedLabel}：<span className="tabular-nums">{formatCreatedAt(item.created_at)}</span>
                  </span>
                  <span className="inline-flex items-center gap-1">
                    {copy.historyImages.detailStatusLabel}：<HistoryStatusBadge status={item.status} />
                  </span>
                  <span>
                    {copy.historyImages.detailCategoryLabel}：{categoryLabel}
                  </span>
                  <span>{copy.historyImages.detailCountLabel(item.item_count)}</span>
                </>
              ) : (
                copy.historyImages.setTitle
              )}
            </DialogDescription>
          </div>
          <DialogClose
            aria-label={copy.historyImages.close}
            className="flex h-8 w-8 flex-none items-center justify-center rounded-mark text-ink-soft outline-none transition-colors hover:bg-glass-hover focus-visible:shadow-focus-gold"
          >
            <X size={16} strokeWidth={2} />
          </DialogClose>
        </div>

        {query.isLoading ? (
          <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-ink-soft" role="status" aria-live="polite">
            <Loader2 size={18} className="animate-spin" /> {copy.historyImages.setLoading}
          </div>
        ) : query.isError ? (
          <div className="flex flex-col items-center gap-3 py-8 text-center">
            <p role="alert" className="text-[13px] text-error-fg">
              {copy.historyImages.setError}
            </p>
            <Button variant="soft" size="sm" onClick={() => void query.refetch()}>
              {copy.historyImages.retry}
            </Button>
          </div>
        ) : set && set.items.length > 0 ? (
          <>
            {set.status === "partial_failed" ? (
              <p className="mb-1 mt-2 text-[12.5px] text-error-fg">{copy.historyImages.setPartialHint}</p>
            ) : null}
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {set.items.map((it) => (
                <HistoryImageTile key={it.index} item={it} />
              ))}
            </div>
          </>
        ) : (
          <p className="py-8 text-center text-[13px] text-ink-soft">{copy.historyImages.setEmpty}</p>
        )}
      </DialogContent>
    </Dialog>
  );
}
