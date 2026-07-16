"use client";

import { MediaLightbox } from "@/components/history/media-lightbox";
import { copy } from "@/lib/copy";

/**
 * 大图弹窗（HISTORY-IMAGE-TAB-UI-0001）：点图片 → 弹**纯图片**大图，不显示任何其它信息（用户原话）。
 * 渲染尺寸目标 ≈ 视口 60%：`max-w:60vw + max-h:60vh` 以**受限的那一维**为准，`<img>` 只给上限、不给固定宽高
 * → 浏览器保持原图比例、**不裁剪**（object-contain 语义）、**不放大超过原图**（max-* 只缩不放，小图停在原尺寸）；
 * 小屏按视口比例自适应。
 *
 * HISTORY-VIDEO-DIALOG-UI-0001：Radix 外壳（焦点陷阱 + ESC + 点遮罩关闭 + aria-modal + sr-only Title/Description
 * + 关闭按钮）已抽到 MediaLightbox 供视频侧复用。**本组件的 props 签名与渲染输出逐字不变** → 调用方
 * (history-grid.tsx) 与既有测试都不动。
 */
export function ImageLightbox({
  src,
  alt,
  open,
  onClose
}: {
  src: string | null;
  alt: string;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <MediaLightbox open={open} onClose={onClose} title={copy.historyImages.lightboxTitle} description={alt}>
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt={alt}
          className="block max-h-[60vh] max-w-[60vw] rounded-card object-contain shadow-focus-gold"
        />
      ) : null}
    </MediaLightbox>
  );
}
