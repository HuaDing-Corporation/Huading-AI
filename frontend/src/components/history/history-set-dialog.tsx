"use client";

import { useCallback } from "react";
import { Loader2 } from "lucide-react";

import { HistoryDetailDialog } from "@/components/history/history-detail-dialog";
import { Button } from "@/components/ui/button";
import { HistoryImageTile } from "@/components/history/history-image-tile";
import { HistoryStatusBadge } from "@/components/history/history-status-badge";
import { useHistoryImageSet } from "@/lib/api/hooks";
import { copy } from "@/lib/copy";
import { useMediaUrlRefreshScope } from "@/lib/media/use-media-url-refresh";
import { historyImageSetMediaKey, type HistoryCategory, type HistoryItem } from "@/lib/api/history-images";

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
 * → 完整展示该次整套（每张原图 download_url + 原始尺寸，原图红线不变）。**并入**「历史生成」卡片
 * 现有信息：生成时间 / 状态徽标 / 分类 / 张数（这些原 HISTORY-UI 弹窗没显）。标题/created_at/status/category 从
 * **列表项 `item` 带入**（BE 详情响应 `ImageHistoryDetailResponse` 无 title，同反推 source_thumbnail_url 模式）。
 * item=null 即关闭（enabled 门控 → 不打开不发请求）。
 *
 * HISTORY-VIDEO-DIALOG-UI-0001：Dialog/尺寸/标题/meta 行/关闭/a11y 已抽到 HistoryDetailDialog 外壳供视频侧复用。
 * **本组件的 props 签名与渲染输出逐字不变** —— 数据获取仍在本组件内（useHistoryImageSet），调用方
 * (history-grid.tsx) 与既有测试都不需要动。
 */
export function HistorySetDialog({ item, onClose }: { item: HistoryItem | null; onClose: () => void }) {
  const query = useHistoryImageSet(item?.category ?? "", item?.id);
  const set = query.data;
  const categoryLabel = item ? (CATEGORY_LABEL[item.category as HistoryCategory] ?? item.category) : "";
  // 🔴 FIX1：**一份预算，整套 N 张共用** —— 一次 refetch 把整套的 download_url 全刷回来，
  // 第 2..N 张各再发一次纯属重复请求。上一版每张 tile 各持一份预算（= 2×N），而且我给它写了条测试
  // 断言「两张失效 → refetch 两次」并标 🔴 —— **测试在给错误行为盖章**。见 use-media-url-refresh.ts。
  const refresh = useMediaUrlRefreshScope(useCallback(() => query.refetch(), [query]));
  return (
    <HistoryDetailDialog
      open={item !== null}
      onClose={onClose}
      title={item?.title ?? copy.historyImages.setTitle}
      titleAttr={item?.title}
      closeLabel={copy.historyImages.close}
      // 🔴 图片域的「提示词」按分类而异 —— 逐个读 BE 源码（FIX1 收准，上一版这条注释是**我新写的假路标**）：
      //  · image_gen   → meta.prompt（services/image_history.py:426，= task.topic，**与标题同源**）
      //  · ecom_model  → meta.extra_prompt（:444 的 else 分支。注意 PhotoHistoryCategory 只有四值
      //                  ["image_gen","ecom_white","ecom_model","cover"]（:26）→ **else 只覆盖 ecom_model**）
      //  · ecom_white  → meta 只有 background / source_asset_id（:431）→ 无提示词（用户只传图 + 选背景）
      //  · cover       → meta 只有 source / timestamp_sec 等（:435）→ 无提示词（只选帧 + 选模板）
      //  · ecom_detail → **走另一个 builder**（:545-559）：meta 是 output_mode / product_info /
      //                  selling_points… → **既无 prompt 也无 extra_prompt**
      //
      // ⚠️ 上一版这里写的是「电商模特 / **详情** / **海报** → meta.extra_prompt」，两处都错：
      //    详情图有自己的 builder、meta 里没有 extra_prompt；「海报」**根本不是历史分类**
      //    （FE HistoryCategory 五值里没有 poster）。实现一直是对的（读两个键、都没有就 null），
      //    **只有注释在撒谎** —— 而注释留在原地就是误导下一个人。
      //
      // 无提示词的三类 → 两个键都取不到 → null → 外壳整块不渲染（不给用户看一个空的「提示词」框）。
      prompt={set?.meta?.prompt ?? set?.meta?.extra_prompt ?? null}
      // 信息并集：生成时间 · 状态 · 分类 · 张数（卡片有、原弹窗没显）。
      meta={
        item ? (
          <>
            <span>
              {copy.historyImages.detailCreatedLabel}：
              <span className="tabular-nums">{formatCreatedAt(item.created_at)}</span>
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
        )
      }
    >
      {query.isLoading ? (
        <div
          className="flex items-center justify-center gap-2 py-10 text-[13px] text-ink-soft"
          role="status"
          aria-live="polite"
        >
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
            {set.items.map((it) => {
              // presign 失效 → 重取本整套（MEDIA-URL-REFRESH-CONVERGE-0001 · 第 5 片 / FIX2 / FIX3）。
              // 数据源就是本组件的 useHistoryImageSet query → 重取拿回的新 download_url 直接喂回 tile，导电。
              // 每张 tile 是独立媒体位置：整套里一张删了、其余在，那一张的无限重试不该被健康张的
              // onLoad 清账（FIX2 的 P1-2）。
              //
              // 🔴 FIX3：上一版这里用 `it.index` 当 mediaKey，注释还写着「it.index 跨 refetch 稳定」——
              // **那句是假的**：白底图/模特图/封面的整套是按 batch_id 聚合多个任务的，BE 只查 `done`
              // 任务再重新 enumerate（image_history.py:84/:472）→ **批量陆续完成时同一张图 index 必然
              // 从 0 漂到 1、2** → 失败预算换到新 key、绕过封顶。身份判据见 historyImageSetMediaKey。
              const mediaKey = historyImageSetMediaKey(set, it);
              return <HistoryImageTile key={mediaKey} item={it} refresh={refresh.forMedia(mediaKey)} />;
            })}
          </div>
        </>
      ) : (
        <p className="py-8 text-center text-[13px] text-ink-soft">{copy.historyImages.setEmpty}</p>
      )}
    </HistoryDetailDialog>
  );
}
