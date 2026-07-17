"use client";

import { useState } from "react";
import { ImageOff, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { HistoryCard } from "@/components/history/history-card";
import { HistorySetDialog } from "@/components/history/history-set-dialog";
import { ImageLightbox } from "@/components/history/image-lightbox";
import { useHistoryImages } from "@/lib/api/hooks";
import { copy } from "@/lib/copy";
import type { HistoryCategory } from "@/lib/api/history-images";

/**
 * 单分类历史网格（HISTORY-IMAGE-TAB-UI-0001）——按 category 分页拉列表（category 省略 = 全部图片），
 * 卡片网格 + 加载/错误/空态 + 「加载更多」。交互：点图 → 大图弹窗；「查看详情」→ 详情弹窗（整套 + 信息并集）。
 * FIX1：归一 API 的图片删除端点被摘掉（用户「三拆」，改 GC 方案将来补）→ 本网格不再有删除入口/确认（GC 包上线时原样复活）。
 * 每个分类各自 useHistoryImages(category)（按 key 缓存）→ 切分类不串数据。
 */
export function HistoryGrid({ category }: { category?: HistoryCategory }) {
  const query = useHistoryImages(category);
  // 🔴 **只存 id，不存列表项快照**（MEDIA-URL-REFRESH-CONVERGE-0001 · 第 4 片）。
  // `cover_url` 是 presign（history-images.ts:19）。存快照 = 弹窗里的 cover_url 冻结在点击那一刻 →
  // 下一片给弹窗挂上 onError + 重取后，refetch 拿回的新 URL **进不到弹窗里** → 得到一个
  // 「测试全绿、线上依然碎图」的防线，比没有防线更危险。**先导电，再挂 onError。**
  // 同 #185 在 generation-history.tsx:44-48 上修过的 P1-1（那次是 playbackUrl，这次是 cover_url）。
  const [lightboxId, setLightboxId] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];

  // 从**最新** items 派生：refetch 一到，弹窗里的 <img src> 自然跟着换。
  // 派生不到（该条已被删）→ null → 弹窗自动关闭，不挂着一个指向已消失记录的界面。
  const lightbox = lightboxId === null ? null : (items.find((i) => i.id === lightboxId) ?? null);
  const detail = detailId === null ? null : (items.find((i) => i.id === detailId) ?? null);

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
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {items.map((item) => (
          <HistoryCard
            key={item.id}
            item={item}
            onOpenImage={() => setLightboxId(item.id)}
            onDetail={() => setDetailId(item.id)}
            onUrlError={() => void query.refetch()}
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

      {/* 按**派生结果**开合、而非 id 是否存在：该条被删后 id 还在、但派生为 null，
          按 id 开就会留下一个空白弹窗。与 generation-history.tsx:156-158 同口径。 */}
      <ImageLightbox
        open={lightbox !== null}
        src={lightbox?.cover_url ?? null}
        alt={lightbox ? copy.historyImages.lightboxAlt(lightbox.title) : ""}
        onClose={() => setLightboxId(null)}
        onUrlError={() => void query.refetch()}
      />
      <HistorySetDialog item={detail} onClose={() => setDetailId(null)} />
    </>
  );
}
