"use client";

import { useState } from "react";
import { Copy } from "lucide-react";

import { Button } from "@/components/ui/button";
import { copyToClipboard } from "@/lib/clipboard";
import { copy } from "@/lib/copy";

/**
 * 「标签 + 全文 + 复制」块（HISTORY-FULL-PROMPT-UI-0001 从 reverse-prompt-result-view 提升为共享组件）。
 *
 * ## 为什么是提升既有的、而不是新写一份
 *
 * 本仓曾有 **3 处独立的 clipboard 实现**，其中两处是坏的（`await navigator.clipboard?.writeText(x)`
 * 的 `?.` 在 API 缺失时短路成 undefined、await 不抛 → 照样置「已复制」→ 谎报）。
 * CLIPBOARD-TRUTH-0001 把这段逻辑**收口到 `@/lib/clipboard` 的 `copyToClipboard`**（返回是否真写进去）——
 * 本组件、copywriting-form、publish-draft-card 三处都调它，成功态一律由返回布尔驱动，全仓再无手写 clipboard。
 * （本组件原先那份是**对的**，但「对的手写拷贝」仍是可被照抄的模板 → 一并收口，见 clipboard.ts 注释。）
 *
 * ## 与原身的差异（FIX1 收准 —— 别把它当原样搬运）
 * 提升时**不是零差异**：新增了 `break-words`（原身只有 `whitespace-pre-wrap`）。
 * 原因：本组件现在也承载**历史里的用户提示词**，那里可能出现超长无空格串（URL / 英文长词）——
 * 只有 `whitespace-pre-wrap` 时它们不折行，会把弹窗横向撑破。行为差异仅此一处，用户可见文案与 props 未变，
 * 原身的 12/12（含下面那条「不谎报」守卫）原样全绿。
 * ⚠️ 「零回归」≠「逐字不变」：前者是测试证明的，后者是更强的断言 —— 这里只成立前者。
 *
 * ## 契约
 * - **不谎报**：Clipboard API 不存在（非安全上下文 / 老浏览器）或写入抛错 → **不置「已复制」**，静默降级
 *   （用户仍可手动选中文本复制）。承重见 copyable-block.test.tsx。
 * - 反馈**不靠颜色**：图标 + 文案一起变（「复制」→「已复制」），1.5s 后复位。
 * - 全文渲染：`whitespace-pre-wrap` 保留换行、`break-words` 让超长无空格串也能折行，**不截断**。
 */
export function CopyableBlock({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  const doCopy = async () => {
    // 仅在**真的写进剪贴板**时才置「已复制」——非安全上下文/抛错一律不谎报（copyToClipboard 返回 false）。
    if (await copyToClipboard(text)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  };
  return (
    <div className="mb-3">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="block text-[12px] tracking-[.5px] text-ink-soft">{label}</span>
        <Button variant="soft" size="sm" onClick={() => void doCopy()}>
          <Copy size={13} strokeWidth={2} /> {copied ? copy.common.copied : copy.common.copy}
        </Button>
      </div>
      <p className="whitespace-pre-wrap break-words rounded-field border border-line-gold bg-glass-soft px-3 py-2 text-[13px] leading-relaxed text-ink">
        {text}
      </p>
    </div>
  );
}
