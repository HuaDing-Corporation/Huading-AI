"use client";

import { X } from "lucide-react";
import type { ReactNode } from "react";

import { CopyableBlock } from "@/components/ui/copyable-block";
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
  /**
   * 🔴 完整提示词（HISTORY-FULL-PROMPT-UI-0001）—— 用户实测：历史里长提示词被标题的 truncate 挡住、看不全，
   * 而他们翻历史的目的**就是把上次的提示词拿回去复用**。
   *
   * **为什么放在外壳而不是做成组件让各调用方自己渲染**：后者是一份**需要人去列全的入口清单** ——
   * 本项目栽过六次的正是「没列全入口」。放外壳 = 谁都漏不掉，是机制不是清单；将来第 3 个调用方接上外壳
   * 就自动有，而不是**静默地没有**。
   *
   * 这不违反本外壳「不把数据提成 props」的原则（见上方注释②）：那条针对的是**内容形态各域不同**的东西
   * （图片是 N 张 tiles、视频是播放器）→ 强行共用会退化成 union + 分支地狱。而提示词各域**渲染完全一致**
   * （一个标签 + 全文 + 复制按钮），差的只是**取哪个字段**，那是调用方的事。共用的仍然是外壳与 a11y。
   *
   * 空/未提供 → 整块不渲染（有的域本来就没有提示词，如电商白底图：用户只传图选背景）。
   */
  prompt?: string | null;
  /** 提示词区块的标签；各域叫法不同（口播是「文案」、生图是「提示词」）→ 由调用方决定。 */
  promptLabel?: string;
}

export function HistoryDetailDialog({
  open,
  onClose,
  title,
  titleAttr,
  meta,
  closeLabel,
  children,
  prompt,
  promptLabel
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
        {/*
         * 提示词块放在**最后**（children 之后），三条理由：
         *  ① **不埋任何东西**：提示词可能几百字。放中间会把下载/打开详情页顶到很深的地方；放最后，它下面
         *     什么都没有 → 长到天上也不影响别的操作。这是 §四.5「不撑爆弹窗」的正解。
         *  ② **不做区域内滚动**：外壳本身已是 max-h-[85vh] overflow-y-auto（见上方 DialogContent），
         *     弹窗结构上就不可能被顶出屏幕。再套一层内滚 = 嵌套滚动区 —— 触屏上会出现滚动链问题
         *     （UI/UX 规则 scroll-behavior：Avoid nested scroll regions that interfere with the main scroll）。
         *     单一滚动区 + 放最后，两个目标一起达成。
         *  ③ **内容优先级**：用户是先看图/视频（他为此而来），再找提示词（他为此而留）。媒体在前，提示词在后。
         * 全文不截断（whitespace-pre-wrap + break-words）—— UI/UX 规则 truncation-strategy：
         * 「Prefer wrapping over truncation」。标题仍截断是对的：标题是**索引**，这里才是**内容**。
         */}
        {prompt ? <CopyableBlock label={promptLabel ?? copy.history.promptLabel} text={prompt} /> : null}
      </DialogContent>
    </Dialog>
  );
}
