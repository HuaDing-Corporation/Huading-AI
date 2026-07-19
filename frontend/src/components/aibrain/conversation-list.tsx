"use client";

// 华鼎AI智脑 · 会话列表（AIBRAIN-UI-0001）。新建 / 切换 / 删除；当前会话高亮（不靠颜色单一：加描边 + aria-current）。

import { useState } from "react";
import { MessageSquarePlus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import { useConversations, useCreateConversation, useDeleteConversation } from "@/lib/aibrain/hooks";
import type { Conversation } from "@/lib/aibrain/types";

export function ConversationList({
  activeId,
  onSelect
}: {
  activeId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const { data, isLoading } = useConversations();
  const create = useCreateConversation();
  const del = useDeleteConversation();
  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null);
  const items = data ?? [];

  const onNew = async () => {
    const conv = await create.mutateAsync();
    onSelect(conv.id);
  };

  const onConfirmDelete = async () => {
    if (!pendingDelete) return;
    const wasActive = pendingDelete.id === activeId;
    await del.mutateAsync(pendingDelete.id);
    setPendingDelete(null);
    if (wasActive) onSelect(null); // 删的是当前会话 → 回空态，不挂着已删记录
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
              <div
                key={conv.id}
                role="listitem"
                className={cn(
                  "group flex items-center gap-1 rounded-field border px-2.5 py-2 transition-colors",
                  active ? "border-line-sel bg-chip-sel" : "border-transparent hover:bg-glass-hover"
                )}
              >
                <button
                  type="button"
                  onClick={() => onSelect(conv.id)}
                  aria-current={active ? "true" : undefined}
                  className="min-w-0 flex-1 truncate text-left text-[13px] text-ink outline-none focus-visible:underline"
                >
                  {conv.title || copy.aibrain.untitled}
                </button>
                <button
                  type="button"
                  aria-label={copy.aibrain.deleteChat}
                  onClick={() => setPendingDelete(conv)}
                  className="flex-none rounded p-1 text-ink-faint opacity-0 outline-none transition-opacity hover:text-error-fg focus-visible:opacity-100 focus-visible:shadow-focus-gold group-hover:opacity-100"
                >
                  <Trash2 size={13} strokeWidth={1.8} />
                </button>
              </div>
            );
          })
        )}
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title={copy.aibrain.deleteChat}
        message={copy.aibrain.deleteChatConfirm}
        confirmLabel={copy.aibrain.deleteChat}
        danger
        submitting={del.isPending}
        onConfirm={() => void onConfirmDelete()}
        onCancel={() => setPendingDelete(null)}
      />
    </aside>
  );
}
