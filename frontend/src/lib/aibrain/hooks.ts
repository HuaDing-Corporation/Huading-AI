// 华鼎AI智脑 · react-query 钩子（AIBRAIN-UI-0001 · FIX1）。读钩子 enabled:!!session；写钩子 onSuccess 失效/回填。

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/lib/auth/auth-context";
import {
  createConversation,
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

/** 充值（topup）。响应是整份钱包 → 直接回填缓存。 */
export function useTopup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (amount: number) => topupWallet({ amount }),
    onSuccess: (wallet: ReasoningWallet) => qc.setQueryData<ReasoningWallet>(aibrainKeys.wallet(), wallet)
  });
}
