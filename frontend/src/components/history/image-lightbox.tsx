"use client";

import { MediaLightbox } from "@/components/history/media-lightbox";
import { copy } from "@/lib/copy";
import type { MediaUrlRefreshScope } from "@/lib/media/use-media-url-refresh";

/**
 * 大图弹窗（HISTORY-IMAGE-TAB-UI-0001）：点图片 → 弹**纯图片**大图，不显示任何其它信息（用户原话）。
 * 渲染尺寸目标 ≈ 视口 60%：`max-w:60vw + max-h:60vh` 以**受限的那一维**为准，`<img>` 只给上限、不给固定宽高
 * → 浏览器保持原图比例、**不裁剪**（object-contain 语义）、**不放大超过原图**（max-* 只缩不放，小图停在原尺寸）；
 * 小屏按视口比例自适应。
 *
 * HISTORY-VIDEO-DIALOG-UI-0001：Radix 外壳（焦点陷阱 + ESC + 点遮罩关闭 + aria-modal + sr-only Title/Description
 * + 关闭按钮）已抽到 MediaLightbox 供视频侧复用。
 *
 * MEDIA-URL-REFRESH-CONVERGE-0001（第 5 片）：props 加 `onUrlError` —— 此前签名逐字不变的承诺到此为止，
 * 因为本组件此前对 presign 过期**零防护**（页面开久了大图全碎、无恢复路径）。
 * 🔴 接防线的**前提**是第 4 片已把调用方从「存 HistoryItem 快照」改成「存 id 从最新 items 派生」——
 * 否则重取拿回的新 URL 进不到这里，防线测试全绿而线上依然碎图，**比没有防线更危险**。
 */
export function ImageLightbox({
  src,
  alt,
  open,
  onClose,
  refresh
}: {
  src: string | null;
  alt: string;
  open: boolean;
  onClose: () => void;
  /**
   * presign 失效重取的作用域，由持有 query 的 HistoryGrid 创建并下发（FIX1）——
   * 本弹窗与网格里的卡片消费**同一个** query，共用一份预算：大图碎了就等于那张卡也碎了，
   * 一次 refetch 两边一起救。收 scope 而非 callback → 结构上不可能各自持一份预算。
   */
  refresh: MediaUrlRefreshScope;
}) {
  return (
    <MediaLightbox open={open} onClose={onClose} title={copy.historyImages.lightboxTitle} description={alt}>
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt={alt}
          onError={() => refresh.onError(src)}
          onLoad={refresh.onLoad}
          className="block max-h-[60vh] max-w-[60vw] rounded-card object-contain shadow-focus-gold"
        />
      ) : null}
    </MediaLightbox>
  );
}
