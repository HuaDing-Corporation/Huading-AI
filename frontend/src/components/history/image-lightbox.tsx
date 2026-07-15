"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";

import { copy } from "@/lib/copy";

/**
 * 大图弹窗（HISTORY-IMAGE-TAB-UI-0001，可复用——包 2 的视频/反推 tab 也用）：点图片 → 弹**纯图片**大图，
 * 不显示任何其它信息（用户原话）。渲染尺寸目标 ≈ 视口 60%：`max-w:60vw + max-h:60vh` 以**受限的那一维**为准，
 * `<img>` 只给上限、不给固定宽高 → 浏览器保持原图比例、**不裁剪**（object-contain 语义）、**不放大超过原图**
 * （max-* 只缩不放，小图停在原尺寸）；小屏按视口比例自适应。基于 Radix Dialog：焦点陷阱 + ESC + 点遮罩关闭 + aria-modal。
 */
export function ImageLightbox({ src, alt, open, onClose }: { src: string | null; alt: string; open: boolean; onClose: () => void }) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/70 backdrop-blur-sm animate-in fade-in-0" />
        <DialogPrimitive.Content className="fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 outline-none animate-in fade-in-0 zoom-in-95">
          {/* Radix 强制要求 Title + Description（a11y）；大图只显图片，故两者 sr-only。 */}
          <DialogPrimitive.Title className="sr-only">{copy.historyImages.lightboxTitle}</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">{alt}</DialogPrimitive.Description>
          {src ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={src} alt={alt} className="block max-h-[60vh] max-w-[60vw] rounded-card object-contain shadow-focus-gold" />
          ) : null}
          <DialogPrimitive.Close
            aria-label={copy.historyImages.close}
            className="absolute -right-3 -top-3 flex h-8 w-8 items-center justify-center rounded-mark bg-ink/70 text-white outline-none transition-colors hover:bg-ink focus-visible:shadow-focus-gold"
          >
            <X size={16} strokeWidth={2} />
          </DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
