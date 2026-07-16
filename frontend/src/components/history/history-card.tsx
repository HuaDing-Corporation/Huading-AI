"use client";

import { Button } from "@/components/ui/button";
import { HistoryStatusBadge } from "@/components/history/history-status-badge";
import { copy } from "@/lib/copy";
import type { HistoryItem } from "@/lib/api/history-images";

/** ISO → "YYYY-MM-DD HH:mm"（确定性、无时区漂移；测试不依赖精确时间）。 */
function formatCreatedAt(iso: string): string {
  return iso.includes("T") ? iso.replace("T", " ").slice(0, 16) : iso;
}

/**
 * 历史网格卡片（HISTORY-IMAGE-TAB-UI-0001）——封面缩略 + 张数角标 + 标题 + 时间 + 状态徽标；
 * 交互（统一交互语言，供包 2 视频 tab 复用同款）：点**图片** → 大图弹窗（onOpenImage）；「查看详情」→ 详情弹窗（onDetail）。
 * FIX1：归一 API 的图片删除端点被摘掉（用户「三拆」，改 GC 方案将来补）→ 本卡**不渲染删除入口**（点了没反应的按钮
 * 比没有更糟）。视频/文案 tab 的删除各走自己旧路径、不受影响；反推删除在包 2。
 */
export function HistoryCard({
  item,
  onOpenImage,
  onDetail
}: {
  item: HistoryItem;
  onOpenImage: () => void;
  onDetail: () => void;
}) {
  return (
    <div data-testid="history-card" className="group flex flex-col gap-2 rounded-card border border-line-gold bg-glass-fill p-2.5">
      <button
        type="button"
        onClick={onOpenImage}
        aria-label={copy.historyImages.openLarge}
        className="relative aspect-square w-full overflow-hidden rounded-mark border border-line-gold bg-glass-soft outline-none focus-visible:shadow-focus-gold"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={item.cover_url} alt={item.title} className="h-full w-full object-cover transition-transform group-hover:scale-[1.02]" />
        <span className="absolute right-1.5 top-1.5 rounded-pill bg-ink/55 px-2 py-0.5 text-[11px] font-medium text-white">
          {copy.historyImages.itemCount(item.item_count)}
        </span>
      </button>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-medium text-ink" title={item.title}>
            {item.title}
          </p>
          <p className="mt-0.5 text-[11.5px] text-ink-faint">{formatCreatedAt(item.created_at)}</p>
        </div>
        <HistoryStatusBadge status={item.status} />
      </div>
      <Button variant="soft" size="sm" className="w-full" onClick={onDetail}>
        {copy.historyImages.viewDetail}
      </Button>
    </div>
  );
}
