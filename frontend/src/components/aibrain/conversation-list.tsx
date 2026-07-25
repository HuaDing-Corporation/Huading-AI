"use client";

// 华鼎AI智脑 · 会话列表（AIBRAIN-UI-0001 · FIX1）。新建 / 切换；当前会话高亮（不靠颜色单一：加描边 + aria-current）。
// 删除 / 清空（HISTORY-CHAT-DELETE-UI-0001，契约 §5.2）：原注释写"BE 无 DELETE 端点 → 本期不提供入口"，
// 本包按冻结文档补上（mock 先行、BE 并行）。E2：**只删整个会话**，不做单条消息删除（保住账本追溯链）。
// 🔴 删的是**当前打开的会话**时不许白屏 → 删成功后自动切走（切到剩余最新一条；没有剩余则 onSelect(null) 走空态）。

import { useState } from "react";
import { MessageSquarePlus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import { useClearConversations, useConversations, useCreateConversation, useDeleteConversation } from "@/lib/aibrain/hooks";

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
  const clear = useClearConversations();
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const items = data ?? [];

  const onNew = async () => {
    try {
      const conv = await create.mutateAsync();
      onSelect(conv.id);
    } catch {
      /* 新建失败：按钮复位即可，不抛未捕获拒绝（CR#6）。 */
    }
  };

  // 🔴 删一条：**删的若是当前打开的会话，必须切走**——否则右侧还挂着一个已不存在的会话 id，
  // 详情查询 404 → 白屏（承重门 3）。切到剩余列表的**第一条**（列表按 updated_at 倒序 = 最近一条）；
  // 一条都不剩 → onSelect(null) 让容器回到空态。失败：不关弹窗、弹窗内报错，列表原样（不乐观移除）。
  const onConfirmDelete = async () => {
    if (!confirmDelete) return;
    setActionError(null);
    try {
      await del.mutateAsync(confirmDelete);
      if (confirmDelete === activeId) {
        const next = items.find((c) => c.id !== confirmDelete);
        onSelect(next ? next.id : null);
      }
      setConfirmDelete(null);
    } catch {
      setActionError(copy.aibrain.deleteChatFailed);
    }
  };

  // 清空全部：删完必然包含当前会话 → 无条件切空态。
  const onConfirmClear = async () => {
    setActionError(null);
    try {
      await clear.mutateAsync();
      onSelect(null);
      setConfirmClear(false);
    } catch {
      setActionError(copy.aibrain.clearChatsFailed);
    }
  };

  return (
    <aside className="flex w-full flex-col gap-2 sm:w-64">
      <Button variant="soft" size="sm" className="justify-start" onClick={() => void onNew()} disabled={create.isPending}>
        <MessageSquarePlus size={15} strokeWidth={1.9} /> {copy.aibrain.newChat}
      </Button>

      <div className="flex flex-col gap-1 overflow-y-auto" aria-label={copy.aibrain.conversationsTitle}>
        {isLoading ? (
          <p className="px-2 py-3 text-[12.5px] text-ink-faint">{copy.aibrain.loading}</p>
        ) : items.length === 0 ? (
          <p className="px-2 py-3 text-[12.5px] text-ink-faint">{copy.aibrain.conversationsEmpty}</p>
        ) : (
          items.map((conv) => {
            const active = conv.id === activeId;
            return (
              // 行容器（非 button）：选择与删除是两个独立命中区——删除按钮不能嵌在会话按钮里（嵌套 button 非法 HTML）。
              <div
                key={conv.id}
                className={cn(
                  "group flex items-center gap-1 rounded-field border pr-1 transition-colors",
                  active ? "border-line-sel bg-chip-sel" : "border-transparent hover:bg-glass-hover"
                )}
              >
                <button
                  type="button"
                  onClick={() => onSelect(conv.id)}
                  aria-current={active ? "true" : undefined}
                  className="min-w-0 flex-1 truncate px-2.5 py-2 text-left text-[13px] text-ink outline-none focus-visible:shadow-focus-gold"
                >
                  {conv.title || copy.aibrain.untitled}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setActionError(null);
                    setConfirmDelete(conv.id);
                  }}
                  aria-label={`${copy.aibrain.deleteChat}：${conv.title || copy.aibrain.untitled}`}
                  title={copy.aibrain.deleteChat}
                  className="flex h-7 w-7 flex-none items-center justify-center rounded-field text-ink-faint outline-none transition-colors hover:bg-error-bg hover:text-error-fg focus-visible:shadow-focus-gold"
                >
                  <Trash2 size={13} strokeWidth={1.8} />
                </button>
              </div>
            );
          })
        )}
      </div>

      {/* 清空全部对话（E3 二次确认）——仅有会话时渲染。 */}
      {items.length > 0 ? (
        <Button
          variant="soft"
          size="sm"
          className="justify-start"
          onClick={() => {
            setActionError(null); // 同 history-grid：两弹窗共享 actionError → 打开前清，避免上次失败的错串台
            setConfirmClear(true);
          }}
          disabled={clear.isPending}
        >
          <Trash2 size={14} strokeWidth={1.8} /> {copy.aibrain.clearChats}
        </Button>
      ) : null}

      {/* 删除确认：文案讲用户可观察后果 +「不影响推理积分余额与账单」（冻结 §5.2：只写 deleted_at，
          不碰 chat_messages / reasoning_ledger / 钱包）。danger：无恢复入口 = 用户视角不可撤销。 */}
      <ConfirmDialog
        open={confirmDelete !== null}
        title={copy.aibrain.deleteChatConfirmTitle}
        message={copy.aibrain.deleteChatConfirmMsg}
        confirmLabel={copy.history.deleteConfirmBtn}
        danger
        submitting={del.isPending}
        error={actionError}
        onConfirm={() => void onConfirmDelete()}
        onCancel={() => {
          setConfirmDelete(null);
          setActionError(null);
        }}
      />
      <ConfirmDialog
        open={confirmClear}
        title={copy.aibrain.clearChatsConfirmTitle}
        message={copy.aibrain.clearChatsConfirmMsg}
        confirmLabel={copy.history.clearConfirmBtn}
        danger
        submitting={clear.isPending}
        error={actionError}
        onConfirm={() => void onConfirmClear()}
        onCancel={() => {
          setConfirmClear(false);
          setActionError(null);
        }}
      />
    </aside>
  );
}
