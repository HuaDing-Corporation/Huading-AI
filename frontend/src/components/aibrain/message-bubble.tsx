"use client";

// 华鼎AI智脑 · 单条消息气泡（AIBRAIN-UI-0001 · FIX1）。
// content 是**单一可追加文本节点**（whitespace-pre-wrap）——增量 2 流式时把 chunk 追加进 content 即可。
// ⚠️ 响应附件（BE ChatAttachmentRead）**只有 asset_id/asset_type/mime_type，无 URL** → 历史消息只显「图片」占位片，不显缩略图。

import { useState } from "react";
import { Check, Copy, Image as ImageIcon } from "lucide-react";

import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import { formatCreditsExact, type ChatMessage } from "@/lib/aibrain/types";

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);

  const doCopy = async () => {
    if (!message.content || !navigator.clipboard?.writeText) return;
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 复制失败静默降级 */
    }
  };

  return (
    <div className={cn("flex flex-col gap-1.5", isUser ? "items-end" : "items-start")}>
      <span className="px-1 text-[11.5px] text-ink-faint">{isUser ? copy.aibrain.you : copy.aibrain.assistant}</span>
      {message.attachments.length > 0 ? (
        <div className={cn("flex flex-wrap gap-2", isUser ? "justify-end" : "justify-start")}>
          {message.attachments.map((att) =>
            att.download_url ? (
              // FIX2：BE 补了 download_url（presign，只签 image）→ 显真缩略图。
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={att.asset_id}
                src={att.download_url}
                alt={copy.aibrain.imageAttachment}
                className="h-16 w-16 rounded-mark border border-line-gold object-cover"
              />
            ) : (
              // download_url 为 null（非 image / 签发失败）→ 降级占位片。
              <span
                key={att.asset_id}
                className="inline-flex items-center gap-1.5 rounded-mark border border-line-gold bg-glass-soft px-2.5 py-1.5 text-[12px] text-ink-soft"
              >
                <ImageIcon size={13} strokeWidth={1.8} className="text-gold-deep" aria-hidden />
                {copy.aibrain.imageAttachment}
              </span>
            )
          )}
        </div>
      ) : null}
      {message.content || message.status === "pending" ? (
        <div
          className={cn(
            "max-w-[min(680px,86%)] whitespace-pre-wrap break-words rounded-card px-4 py-2.5 text-[14px] leading-relaxed",
            isUser ? "bg-grad-gold text-ink shadow-button" : "border border-line-gold bg-glass-fill text-ink"
          )}
        >
          {message.content}
          {message.status === "pending" ? (
            <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-ink-soft align-middle" aria-hidden />
          ) : null}
        </div>
      ) : null}
      {!isUser && message.content ? (
        <div className="flex items-center gap-2 px-1">
          <button
            type="button"
            onClick={() => void doCopy()}
            className="inline-flex items-center gap-1 rounded-field px-1.5 py-0.5 text-[11.5px] text-ink-faint outline-none transition-colors hover:text-ink-soft focus-visible:shadow-focus-gold"
          >
            {copied ? <Check size={12} strokeWidth={2} /> : <Copy size={12} strokeWidth={2} />}
            {copied ? copy.aibrain.copied : copy.aibrain.copy}
          </button>
          {message.charged_credits != null ? (
            <span className="text-[11px] text-ink-faint tabular-nums">{copy.aibrain.costLabel(formatCreditsExact(message.charged_credits))}</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
