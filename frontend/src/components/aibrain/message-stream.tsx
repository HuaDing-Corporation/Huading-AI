"use client";

// 华鼎AI智脑 · 消息流（AIBRAIN-UI-0001）。role=log + aria-live=polite → 屏幕阅读器播报新（含未来流式）内容。

import { Loader2, MessageCircle } from "lucide-react";

import { copy } from "@/lib/copy";
import type { ChatMessage } from "@/lib/aibrain/types";
import { MessageBubble } from "@/components/aibrain/message-bubble";

export function MessageStream({
  messages,
  pending
}: {
  messages: ChatMessage[];
  /** 已发出用户消息、等待助手回答（非流式一期：一个「正在思考」占位）。 */
  pending?: boolean;
}) {
  if (messages.length === 0 && !pending) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 py-16 text-center" role="status">
        <MessageCircle size={30} strokeWidth={1.5} className="text-ink-faint" aria-hidden />
        <p className="text-[14px] text-ink">{copy.aibrain.emptyTitle}</p>
        <p className="text-[12.5px] text-ink-faint">{copy.aibrain.emptyHint}</p>
      </div>
    );
  }

  return (
    <div role="log" aria-live="polite" aria-label={copy.aibrain.assistant} className="flex flex-1 flex-col gap-5 py-4">
      {messages.map((m) => (
        <MessageBubble key={m.id} message={m} />
      ))}
      {pending ? (
        <div className="flex items-center gap-2 px-1 text-[12.5px] text-ink-soft" role="status">
          <Loader2 size={15} className="animate-spin" aria-hidden /> {copy.aibrain.thinking}
        </div>
      ) : null}
    </div>
  );
}
