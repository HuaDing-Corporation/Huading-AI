"use client";

import { Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { HistoryStatusBadge } from "@/components/history/history-status-badge";
import { copy } from "@/lib/copy";
import type { MediaUrlRefreshScope } from "@/lib/media/use-media-url-refresh";
import type { HistoryItem } from "@/lib/api/history-images";

/** ISO → "YYYY-MM-DD HH:mm"（确定性、无时区漂移；测试不依赖精确时间）。 */
function formatCreatedAt(iso: string): string {
  return iso.includes("T") ? iso.replace("T", " ").slice(0, 16) : iso;
}

/**
 * 历史网格卡片（HISTORY-IMAGE-TAB-UI-0001）——封面缩略 + 张数角标 + 标题 + 时间 + 状态徽标；
 * 交互（统一交互语言，供包 2 视频 tab 复用同款）：点**图片** → 大图弹窗（onOpenImage）；「查看详情」→ 详情弹窗（onDetail）。
 *
 * 删除入口（HISTORY-CHAT-DELETE-UI-0001，FIX1 曾摘掉、本包按冻结 §二复活为纯记录软删）：
 * 🔴 **放底部操作行、不放缩略图角**——缩略图右上已被张数角标占着（:56），#218 的教训正是「徽标与删除按钮
 * 抢同一个角 → 有徽标的条目删不掉」，那次的结论是「结构上不可能再同角竞争」。这里让删除与「查看详情」
 * 同处底部一行（各自独立命中区，图标按钮 h-8 w-8 ≥ 触控下限），缩略图区域**只有**一个角标、零竞争。
 */
export function HistoryCard({
  item,
  onOpenImage,
  onDetail,
  onDelete,
  refresh
}: {
  item: HistoryItem;
  onOpenImage: () => void;
  onDetail: () => void;
  /** 删除入口；父级（HistoryGrid）持确认弹窗与 mutation。 */
  onDelete: () => void;
  /**
   * presign 失效重取的**作用域**，由持有 query 的 HistoryGrid 创建并下发（FIX1）。
   *
   * 🔴 收 scope 而非 `onUrlError: () => void`：整个网格的卡片消费的是**同一个** useHistoryImages query，
   * 一次 refetch 就把所有 cover_url 刷回来了。若每张卡各持一份预算（上一版就是），全碎时 = **2×N 次**
   * 真实请求 —— 局部正确、全局错。预算属于 query，元素只负责报告自己的 URL。
   *
   * 本卡的 item 来自 `items.map(...)` **派生**（非快照）→ 新 cover_url 喂得进来，防线导电（第 4 片实测）。
   */
  refresh: MediaUrlRefreshScope;
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
        <img
          src={item.cover_url}
          alt={item.title}
          onError={() => refresh.onError(item.cover_url)}
          onLoad={refresh.onLoad}
          className="h-full w-full object-cover transition-transform group-hover:scale-[1.02]"
        />
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
      {/* 底部操作行：详情占主宽、删除是独立图标按钮（与缩略图角标物理隔离——见组件头注释的 #218 教训）。 */}
      <div className="flex items-center gap-2">
        <Button variant="soft" size="sm" className="flex-1" onClick={onDetail}>
          {copy.historyImages.viewDetail}
        </Button>
        <button
          type="button"
          onClick={onDelete}
          aria-label={copy.history.deleteItem}
          title={copy.history.deleteItem}
          className="flex h-8 w-8 flex-none items-center justify-center rounded-field border border-line-gold text-ink-soft outline-none transition-colors hover:bg-error-bg hover:text-error-fg focus-visible:shadow-focus-gold"
        >
          <Trash2 size={14} strokeWidth={1.8} />
        </button>
      </div>
    </div>
  );
}
