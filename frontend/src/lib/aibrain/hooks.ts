// 华鼎AI智脑 · react-query 钩子（AIBRAIN-UI-0001 · FIX1）。读钩子 enabled:!!session；写钩子 onSuccess 失效/回填。

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/lib/auth/auth-context";
import {
  clearConversations,
  createConversation,
  deleteConversation,
  getConversation,
  getWallet,
  listConversations,
  sendMessage,
  topupWallet
} from "@/lib/aibrain/api";
import { aibrainKeys } from "@/lib/aibrain/keys";
import type { ReasoningWallet, SendMessageRequest } from "@/lib/aibrain/types";

export function useConversations() {
  const { session } = useAuth();
  return useQuery({ queryKey: aibrainKeys.conversations(), queryFn: listConversations, enabled: !!session });
}

export function useConversation(id: string | undefined) {
  const { session } = useAuth();
  return useQuery({
    queryKey: aibrainKeys.conversation(id ?? ""),
    queryFn: () => getConversation(id as string),
    enabled: !!session && !!id
  });
}

export function useWallet() {
  const { session } = useAuth();
  return useQuery({ queryKey: aibrainKeys.wallet(), queryFn: getWallet, enabled: !!session });
}

export function useCreateConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => createConversation(),
    onSuccess: () => void qc.invalidateQueries({ queryKey: aibrainKeys.conversations() })
  });
}

// ── 会话删除 / 清空（HISTORY-CHAT-DELETE-UI-0001，E2 只删整会话）────────────────
// 只失效**会话列表**：被删会话的详情键不主动清（BE 软删后详情按契约 404；列表刷新后 UI 已切走，
// 留着的旧详情缓存无消费者，主动 remove 反而要多担一份"删哪个键"的耦合）。
// 🔴 不碰 wallet 键：删会话不动账本/钱包（冻结 §5.2）——验收 4 要求余额与账单一分不变，
// 这里**不失效 wallet** 正是它的前端侧保证（失效会触发重取，虽不改值但会掩盖 BE 侧异常）。
export function useDeleteConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteConversation(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: aibrainKeys.conversations() })
  });
}

export function useClearConversations() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => clearConversations(),
    onSuccess: () => void qc.invalidateQueries({ queryKey: aibrainKeys.conversations() })
  });
}

/**
 * 发消息。conversationId 走**变量**（支持「首条消息先建会话再发」的链）。答完后：
 * ① 失效本会话详情（拿到新消息）② 失效会话列表（标题/updated_at 可能变）
 * ③ 把响应里的**整份钱包**（BE `wallet`，非 balance）回填缓存（多退少补后的真实余额，不必再拉一次）。
 */
export function useSendMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { conversationId: string; body: SendMessageRequest }) =>
      sendMessage(vars.conversationId, vars.body),
    onSuccess: (res, vars) => {
      void qc.invalidateQueries({ queryKey: aibrainKeys.conversation(vars.conversationId) });
      void qc.invalidateQueries({ queryKey: aibrainKeys.conversations() });
      qc.setQueryData<ReasoningWallet>(aibrainKeys.wallet(), res.wallet);
    }
  });
}

/**
 * 充值（topup）。响应是整份钱包 → 直接回填缓存。
 * 🔴 幂等键由**调用方（充值弹窗）**给并跨重试复用（§四之二）——不在这里生成，否则每次 mutate 都是新 key = 没有幂等。
 */
export function useTopup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { amount: number; idempotencyKey: string }) =>
      topupWallet({ amount: vars.amount, idempotency_key: vars.idempotencyKey }),
    onSuccess: (wallet: ReasoningWallet) => qc.setQueryData<ReasoningWallet>(aibrainKeys.wallet(), wallet)
  });
}
