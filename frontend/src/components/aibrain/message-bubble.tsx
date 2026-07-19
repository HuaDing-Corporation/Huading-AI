"use client";

// 华鼎AI智脑 · 单条消息气泡（AIBRAIN-UI-0001）。
// 🔴 content 是**单一可追加文本节点**（whitespace-pre-wrap）——增量 2 流式时把 chunk 追加进 content 即可，不必重写。

import { useState } from "react";
import { Check, Copy, FileText } from "lucide-react";

import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/lib/aibrain/types";

function AttachmentThumb({ att }: { att: NonNullable<ChatMessage["attachments"]>[number] }) {
  if (att.kind === "image" && att.preview_url) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={att.preview_url}
        alt={att.name}
        className="h-16 w-16 rounded-mark border border-line-gold object-cover"
      />
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-mark border border-line-gold bg-glass-soft px-2.5 py-1.5 text-[12px] text-ink-soft">
      <FileText size={13} strokeWidth={1.8} className="text-gold-deep" aria-hidden />
      <span className="max-w-[140px] truncate">{att.name}</span>
      {att.doc_status ? <span className="text-ink-faint">· {copy.aibrain.docReceived}</span> : null}
    </span>
  );
}

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
      {message.attachments?.length ? (
        <div className={cn("flex flex-wrap gap-2", isUser ? "justify-end" : "justify-start")}>
          {message.attachments.map((att, i) => (
            <AttachmentThumb key={`${att.ref}-${i}`} att={att} />
          ))}
        </div>
      ) : null}
      {message.content || message.status === "streaming" ? (
        <div
          className={cn(
            "max-w-[min(680px,86%)] whitespace-pre-wrap break-words rounded-card px-4 py-2.5 text-[14px] leading-relaxed",
            isUser
              ? "bg-grad-gold text-ink shadow-button"
              : "border border-line-gold bg-glass-fill text-ink"
          )}
        >
          {message.content}
          {message.status === "streaming" ? (
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
          {message.cost_credits != null ? (
            <span className="text-[11px] text-ink-faint tabular-nums">{copy.aibrain.costLabel(message.cost_credits)}</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
