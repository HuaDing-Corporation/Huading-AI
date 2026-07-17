"use client";

import { X } from "lucide-react";
import type { ReactNode } from "react";

import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { copy } from "@/lib/copy";

/**
 * 历史详情弹窗**外壳**（HISTORY-VIDEO-DIALOG-UI-0001，从 HistorySetDialog 抽出）——只负责表现层与 a11y：
 * Dialog 语义 / 尺寸与滚动 / 标题（截断 + hover 全文）/ 标题下的信息并集行 / 关闭按钮；Esc 与 focus trap 由
 * Radix Dialog 提供。内容与三态（loading/error/empty）由各调用方以 children 传入。
 *
 * 为什么抽「外壳」而不是把数据提成 props：
 *  ① **零回归的最短路径**：图片侧 HistorySetDialog 已上线、用户在用。抽外壳后它的 props 签名与渲染输出**逐字
 *     不变** → 调用方（history-grid.tsx）不动、既有测试不用改写。把数据提成 props 则要改它的签名 → 调用方改 →
 *     既有测试改，而「改既有测试的期望值」是最容易掩盖问题的动作。
 *  ② 图片详情（整套 N 张 tiles + 原图红线）与视频详情（播放器 + 元信息）**内容本就不同**，强行共用一个 data
 *     prop 会退化成 union + 分支地狱 —— 那是伪复用。真正该共用的是外壳与 a11y。
 *  ③ 三态文案各域不同（图片用 copy.historyImages.set*）→ 留给调用方，外壳不替已上线的文案做决定。
 */
export interface HistoryDetailDialogProps {
  open: boolean;
  onClose: () => void;
  /** 标题（长标题截断，全文见 titleAttr）。 */
  title: string;
  /** 标题 hover 全文。 */
  titleAttr?: string;
  /** 标题下的信息并集行：生成时间 / 状态 / 分类 / 张数…由调用方决定该域显示什么。 */
  meta?: ReactNode;
  /** 关闭按钮的可及名；缺省沿用图片历史的既有文案。 */
  closeLabel?: string;
  /** 弹窗主体：调用方自行渲染 loading / error / 内容 / 空态。 */
  children?: ReactNode;
}

export function HistoryDetailDialog({
  open,
  onClose,
  title,
  titleAttr,
  meta,
  closeLabel,
  children
}: HistoryDetailDialogProps) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="w-[min(94vw,880px)] max-h-[85vh] overflow-y-auto">
        <div className="mb-1 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <DialogTitle className="truncate text-base font-semibold text-ink" title={titleAttr}>
              {title}
            </DialogTitle>
            <DialogDescription className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-soft">
              {meta}
            </DialogDescription>
          </div>
          <DialogClose
            aria-label={closeLabel ?? copy.historyImages.close}
            className="flex h-8 w-8 flex-none items-center justify-center rounded-mark text-ink-soft outline-none transition-colors hover:bg-glass-hover focus-visible:shadow-focus-gold"
          >
            <X size={16} strokeWidth={2} />
          </DialogClose>
        </div>
        {children}
      </DialogContent>
    </Dialog>
  );
}
