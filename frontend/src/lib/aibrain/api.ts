// 华鼎AI智脑 · API adapter（AIBRAIN-UI-0001 · FIX1 对齐 BE f2e9a2e0）。复用 client.apiFetch（鉴权/401/封套单一实现）。
// ⚠️ BE 增量 1 **无** DELETE 会话、**无**文档上传（那是增量 3）→ 本文件不含这两个 adapter（不在一期路径调用）。

import { apiFetch } from "@/lib/api/client";
import type {
  Conversation,
  ConversationDetail,
  ConversationListResponse,
  ReasoningWallet,
  SendMessageRequest,
  SendMessageResponse,
  TopupRequest
} from "@/lib/aibrain/types";

const BASE = "/api/v1/aibrain";

export async function listConversations(): Promise<Conversation[]> {
  const res = await apiFetch<ConversationListResponse>(`${BASE}/conversations`, { method: "GET" });
  return res?.items ?? [];
}

export function createConversation(): Promise<ConversationDetail> {
  // BE `ConversationCreateRequest`（extra=forbid，title 可选）→ 201 ConversationRead。
  return apiFetch<ConversationDetail>(`${BASE}/conversations`, { method: "POST", body: {} });
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return apiFetch<ConversationDetail>(`${BASE}/conversations/${encodeURIComponent(id)}`, { method: "GET" });
}

/** 发消息（一期非流式）。**tier 必传 + 附件是 attachment_asset_ids**（BE extra=forbid，多传字段即 422）。 */
export function sendMessage(conversationId: string, body: SendMessageRequest): Promise<SendMessageResponse> {
  return apiFetch<SendMessageResponse>(`${BASE}/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    body
  });
}

export function getWallet(): Promise<ReasoningWallet> {
  return apiFetch<ReasoningWallet>(`${BASE}/wallet`, { method: "GET" });
}

/** 充值（BE `POST /wallet/topup`，非 recharge）。余额 1:1 → 推理积分；单向不可退。响应是整份钱包。 */
export function topupWallet(body: TopupRequest): Promise<ReasoningWallet> {
  return apiFetch<ReasoningWallet>(`${BASE}/wallet/topup`, { method: "POST", body });
}
