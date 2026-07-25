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

// ── 会话删除（HISTORY-CHAT-DELETE-UI-0001，契约 §5.2）───────────────────────────
// 语义：**只写 chat_conversations.deleted_at**——不碰 chat_messages、不碰 reasoning_ledger_entries、不碰钱包。
// 冻结文档 §3.1 的硬结论：硬删会话会经 DB 级 CASCADE 连带删消息，随后账本 chat_message_id 被 SET NULL
// （**绕过 #206 的 ORM 层钱包/账本守卫、不报警**）→ 金额还在但追溯链断。故只软删会话。E2：不做单条消息删除。
// 跨租户 → 404。⚠️ mock 先行（BE aibrain 路由本无任何 DELETE，见冻结 §3.3），BE 合并后真联调对齐。

/** 单条删除响应（与图片历史/反推同形）。 */
export interface ConversationDeletedResponse {
  deleted: boolean;
}

/** 清空全部会话响应。 */
export interface ConversationsClearedResponse {
  deleted_count: number;
}

export function deleteConversation(id: string): Promise<ConversationDeletedResponse> {
  return apiFetch<ConversationDeletedResponse>(`${BASE}/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** 清空**全部**会话（智脑侧无分类，故此处就是全清；单事务批量软删）。 */
export function clearConversations(): Promise<ConversationsClearedResponse> {
  return apiFetch<ConversationsClearedResponse>(`${BASE}/conversations`, { method: "DELETE" });
}

export function getWallet(): Promise<ReasoningWallet> {
  return apiFetch<ReasoningWallet>(`${BASE}/wallet`, { method: "GET" });
}

/** 充值（BE `POST /wallet/topup`，非 recharge）。余额 1:1 → 推理积分；单向不可退。响应是整份钱包。 */
export function topupWallet(body: TopupRequest): Promise<ReasoningWallet> {
  return apiFetch<ReasoningWallet>(`${BASE}/wallet/topup`, { method: "POST", body });
}
