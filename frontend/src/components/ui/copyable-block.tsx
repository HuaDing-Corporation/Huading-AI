"use client";

import { useState } from "react";
import { Copy } from "lucide-react";

import { Button } from "@/components/ui/button";
import { copy } from "@/lib/copy";

/**
 * 「标签 + 全文 + 复制」块（HISTORY-FULL-PROMPT-UI-0001 从 reverse-prompt-result-view 提升为共享组件）。
 *
 * ## 为什么是提升既有的、而不是新写一份
 *
 * 本仓已有 **3 处独立的 clipboard 实现**，而且**已经分叉、其中两处是坏的**（实测）：
 *  - `workbench/reverse-prompt-result-view.tsx`（本组件的原身）：`if (!text || !navigator.clipboard?.writeText) return;`
 *    —— **正确**：Clipboard API 缺失时直接不动，不置「已复制」。
 *  - `workbench/copywriting-form.tsx:90`：`await navigator.clipboard?.writeText(text)` —— `?.` 在
 *    `navigator.clipboard` 缺失时**短路成 undefined**，`await undefined` **不抛异常** → 紧跟的
 *    `setCopiedFlash(true)` 照样执行 → **界面说「已复制」，剪贴板里什么都没有**。
 *  - `publish/publish-draft-card.tsx:48`：同一个 `?.` 短路，同样谎报成功。
 *
 * 也就是说：有人发现过这个 bug、**只修了一处**（原身的注释与承重测试都写着「Review P3 修正」），
 * 另外两处原样留着。**拷贝不只是变多，它会悄悄漂移** —— 而漂移没有任何测试拦得住，因为每处各测各的。
 * 所以本包**不写第四份**，而是把正确的那份提升出来共用。
 * （另外两处属 workbench / publish 域、不在本包范围 —— 已报 backlog，本包不顺手改。）
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
    // 仅在 Clipboard API 存在且写入成功时才置「已复制」——避免非安全上下文(clipboard 缺失)下谎报成功态。
    if (!text || !navigator.clipboard?.writeText) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // 复制失败静默降级（不显示「已复制」）
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
