"use client";

import { HistoryStatusBadge } from "@/components/history/history-status-badge";
import { copy } from "@/lib/copy";
import type { HistoryItem } from "@/lib/api/history-images";

/** ISO → "YYYY-MM-DD HH:mm"（确定性、无时区漂移；测试不依赖精确时间）。 */
function formatCreatedAt(iso: string): string {
  return iso.includes("T") ? iso.replace("T", " ").slice(0, 16) : iso;
}

/**
 * 历史网格卡片（HISTORY-UI-0001）——封面缩略 + 张数角标 + 标题 + 时间 + 状态徽标。整卡为按钮：点开「重开整套」弹窗。
 */
export function HistoryCard({ item, onOpen }: { item: HistoryItem; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex flex-col gap-2 rounded-card border border-line-gold bg-glass-fill p-2.5 text-left outline-none transition-colors hover:bg-glass-hover focus-visible:shadow-focus-gold"
    >
      <div className="relative aspect-square w-full overflow-hidden rounded-mark border border-line-gold bg-glass-soft">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={item.cover_url} alt={item.title} className="h-full w-full object-cover transition-transform group-hover:scale-[1.02]" />
        <span className="absolute right-1.5 top-1.5 rounded-pill bg-ink/55 px-2 py-0.5 text-[11px] font-medium text-white">
          {copy.historyImages.itemCount(item.item_count)}
        </span>
      </div>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-medium text-ink" title={item.title}>
            {item.title}
          </p>
          <p className="mt-0.5 text-[11.5px] text-ink-faint">{formatCreatedAt(item.created_at)}</p>
        </div>
        <HistoryStatusBadge status={item.status} />
      </div>
    </button>
  );
}
