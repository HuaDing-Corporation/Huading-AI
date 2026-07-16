"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";

import { copy } from "@/lib/copy";

/**
 * 大图/大屏弹窗**外壳**（HISTORY-VIDEO-DIALOG-UI-0001，从 ImageLightbox 抽出，供图片与视频共用）。
 * 只负责：Radix Dialog（焦点陷阱 + ESC + 点遮罩关闭 + aria-modal）、遮罩、居中定位、关闭按钮，
 * 以及 Radix 强制要求的 Title/Description（内容区只放媒体，故两者 sr-only）。
 *
 * 与 HistoryDetailDialog 同款思路：抽的是外壳与 a11y，**不是**把媒体类型做成 union prop。
 * 图片要 `<img max-h/max-w + object-contain>`（只缩不放、不裁剪 —— 原图红线），视频要 `<video controls>`
 * （有固有比例、controls 需要空间），尺寸策略本就不同 → 各自渲染 children，外壳不掺和。
 * ImageLightbox 的 props 签名与渲染输出因此逐字不变 → 图片 tab 零回归、既有调用方与测试不动。
 */
export function MediaLightbox({
  open,
  onClose,
  title,
  description,
  children
}: {
  open: boolean;
  onClose: () => void;
  /** sr-only 标题（Radix 强制）。 */
  title: string;
  /** sr-only 描述（Radix 强制）——通常是媒体的可及描述。 */
  description: string;
  children?: ReactNode;
}) {
  return (
    <DialogPrimitive.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/70 backdrop-blur-sm animate-in fade-in-0" />
        <DialogPrimitive.Content className="fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 outline-none animate-in fade-in-0 zoom-in-95">
          {/* Radix 强制要求 Title + Description（a11y）；内容区只显媒体，故两者 sr-only。 */}
          <DialogPrimitive.Title className="sr-only">{title}</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">{description}</DialogPrimitive.Description>
          {children}
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
