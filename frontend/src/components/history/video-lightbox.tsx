"use client";

import { MediaLightbox } from "@/components/history/media-lightbox";
import { copy } from "@/lib/copy";
import type { MediaUrlRefreshScope } from "@/lib/media/use-media-url-refresh";

/**
 * 视频大屏弹窗（HISTORY-VIDEO-DIALOG-UI-0001）—— 图片 lightbox 的视频等价物。
 *
 * **「大图」对视频意味着什么**（spec 判断）：**overlay 内内联播放**，不是放大静止封面。图片 lightbox 的语义
 * 是「看清原图细节」，视频的等价物是「看清内容」= 播放；放大一张静止封面几乎零信息增量（卡片上已有同一张）。
 *
 * a11y / 交互规则：
 *  - **不 autoplay**：历史视频含口播（有声）→ 有声 autoplay 会被浏览器拦截，而静音自动播对口播视频毫无意义；
 *    自动出声也违反「用户发起」原则。故打开只显首帧（poster）+ 原生 controls，由用户点播放。
 *    连带好处：prefers-reduced-motion 用户不会被声音/动态突袭，无需额外分支。
 *  - **关闭即卸载**（硬要求）：Radix Portal 在 open=false 时不渲染内容 → `<video>` 卸载 → 播放立即停止。
 *    留着播 = 用户切走后还在响。
 *  - focus trap / ESC / 点遮罩关闭：由 Radix Dialog（经 MediaLightbox）提供。
 *  - `<video>` 带 aria-label（视频主题），否则读屏只报「video」。
 *  - 尺寸比图片 lightbox 略大（70vh/80vw）：视频需要观看面积，且原生 controls 要占一条。
 *  - 🔴 **`playsInline`（FIX1）**：缺了它，iPhone Safari 会在点播放的瞬间把视频**接管进系统全屏播放器** ——
 *    overlay 里什么都不会发生。而本组件存在的全部理由就是「overlay 内**内联**播放」（见上文「大图」论证），
 *    所以在移动 Safari 上，没有 playsInline 就等于这个组件白写了。它不是锦上添花，是本组件的**成立条件**。
 *  - 🔴 **presign 失效重取（FIX1）**：页面开着超过 presign TTL 再打开 → 旧 URL 播不了。
 *    走共享的 `useMediaUrlRefresh`（**不再手抄第 N 份哨兵** —— 本仓现有三处拷贝已经三个行为了，见该 hook 注释）。
 */
export function VideoLightbox({
  src,
  poster,
  title,
  open,
  onClose,
  refresh
}: {
  src: string | null;
  poster?: string | null;
  /** 视频主题 —— 用作 `<video>` 的可及名与 sr-only 描述。 */
  title: string;
  open: boolean;
  onClose: () => void;
  /**
   * presign 失效重取的作用域，由持有 query 的调用方创建并下发（FIX1）——
   * 本 overlay 与列表里的卡片消费**同一个** query，共用一份预算：overlay 里的 URL 碎了，
   * 那张卡的也碎了，一次 refetch 两边一起救。收 scope 而非 callback → 不可能各自持一份预算。
   */
  refresh: MediaUrlRefreshScope;
}) {
  return (
    <MediaLightbox open={open} onClose={onClose} title={copy.history.videoLightboxTitle} description={title}>
      {src ? (
        <video
          controls
          playsInline
          preload="metadata"
          poster={poster ?? undefined}
          src={src}
          aria-label={title}
          onError={() => refresh.onError(src)}
          onLoadedMetadata={refresh.onLoad}
          className="block max-h-[70vh] max-w-[80vw] rounded-card bg-black shadow-focus-gold"
        />
      ) : null}
    </MediaLightbox>
  );
}
