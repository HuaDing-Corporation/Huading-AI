"use client";

import { Loader2, X } from "lucide-react";

import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { HistoryImageTile } from "@/components/history/history-image-tile";
import { useHistoryImageSet } from "@/lib/api/hooks";
import { copy } from "@/lib/copy";
import type { HistoryItem } from "@/lib/api/history-images";

/**
 * 重开整套弹窗（HISTORY-UI-0001 核心）——点历史卡片 → 拉 GET /history/images/{category}/{id} → 完整展示该次整套
 * （详情图 5/12 张、模特套图等；单图类单张）。每张走 HistoryImageTile（原图红线：下载原图、原始尺寸、缺失禁用）。
 * item=null 即关闭（enabled 门控 → 不打开不发请求）。
 */
export function HistorySetDialog({ item, onClose }: { item: HistoryItem | null; onClose: () => void }) {
  const query = useHistoryImageSet(item?.category ?? "", item?.id);
  const set = query.data;
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
            <DialogTitle className="text-base font-semibold text-ink">{item?.title ?? copy.historyImages.setTitle}</DialogTitle>
            <DialogDescription className="mt-1 text-[12.5px] text-ink-soft">{copy.historyImages.setTitle}</DialogDescription>
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
