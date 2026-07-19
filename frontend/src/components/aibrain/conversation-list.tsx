"use client";

// 华鼎AI智脑 · 会话列表（AIBRAIN-UI-0001 · FIX1）。新建 / 切换；当前会话高亮（不靠颜色单一：加描边 + aria-current）。
// ⚠️ BE 增量 1 无 DELETE 会话端点 → **本期不提供删除入口**（不硬塞、不在一期路径调用）。

import { MessageSquarePlus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import { useConversations, useCreateConversation } from "@/lib/aibrain/hooks";

export function ConversationList({
  activeId,
  onSelect
}: {
  activeId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const { data, isLoading } = useConversations();
  const create = useCreateConversation();
  const items = data ?? [];

  const onNew = async () => {
    const conv = await create.mutateAsync();
    onSelect(conv.id);
  };

  return (
    <aside className="flex w-full flex-col gap-2 sm:w-64">
      <Button variant="soft" size="sm" className="justify-start" onClick={() => void onNew()} disabled={create.isPending}>
        <MessageSquarePlus size={15} strokeWidth={1.9} /> {copy.aibrain.newChat}
      </Button>

      <div className="flex flex-col gap-1 overflow-y-auto" role="list" aria-label={copy.aibrain.conversationsTitle}>
        {isLoading ? (
          <p className="px-2 py-3 text-[12.5px] text-ink-faint">{copy.aibrain.loading}</p>
        ) : items.length === 0 ? (
          <p className="px-2 py-3 text-[12.5px] text-ink-faint">{copy.aibrain.conversationsEmpty}</p>
        ) : (
          items.map((conv) => {
            const active = conv.id === activeId;
            return (
              <button
                key={conv.id}
                type="button"
                role="listitem"
                onClick={() => onSelect(conv.id)}
                aria-current={active ? "true" : undefined}
                className={cn(
                  "truncate rounded-field border px-2.5 py-2 text-left text-[13px] text-ink outline-none transition-colors focus-visible:shadow-focus-gold",
                  active ? "border-line-sel bg-chip-sel" : "border-transparent hover:bg-glass-hover"
                )}
              >
                {conv.title || copy.aibrain.untitled}
              </button>
            );
          })
        )}
      </div>
    </aside>
  );
}
