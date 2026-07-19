// 华鼎AI智脑 · API adapter（AIBRAIN-UI-0001）。纯函数，复用 client.apiFetch / multipartFetch（鉴权/401/封套单一实现）。
// 端点/字段镜像 BE 增量 1（D7）；BE 合并后逐字段复核。图片复用既有 /uploads（D6）。

import { apiFetch, multipartFetch } from "@/lib/api/client";
import type {
  ChatAttachment,
  Conversation,
  ConversationDetail,
  ConversationListResponse,
  RechargeRequest,
  RechargeResponse,
  ReasoningWallet,
  SendMessageRequest,
  SendMessageResponse
} from "@/lib/aibrain/types";

const BASE = "/api/v1/aibrain";

export async function listConversations(): Promise<Conversation[]> {
  const res = await apiFetch<ConversationListResponse>(`${BASE}/conversations`, { method: "GET" });
  return res?.items ?? [];
}

export function createConversation(): Promise<Conversation> {
  return apiFetch<Conversation>(`${BASE}/conversations`, { method: "POST", body: {} });
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return apiFetch<ConversationDetail>(`${BASE}/conversations/${encodeURIComponent(id)}`, { method: "GET" });
}

export function deleteConversation(id: string): Promise<{ id: string }> {
  return apiFetch<{ id: string }>(`${BASE}/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** 发消息（一期非流式）。**tier 必传**（承重：档位随请求传）。 */
export function sendMessage(conversationId: string, body: SendMessageRequest): Promise<SendMessageResponse> {
  return apiFetch<SendMessageResponse>(`${BASE}/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    body
  });
}

export function getWallet(): Promise<ReasoningWallet> {
  return apiFetch<ReasoningWallet>(`${BASE}/wallet`, { method: "GET" });
}

/** 充值（余额 1:1 → 推理积分；单向不可退，D4）。 */
export function rechargeWallet(body: RechargeRequest): Promise<RechargeResponse> {
  return apiFetch<RechargeResponse>(`${BASE}/wallet/recharge`, { method: "POST", body });
}

/**
 * 文档上传（pdf/docx/txt）。一期只**收下**、mock 返回「已收到/解析中」（D6：解析在 BE 增量 3，别假装已解析）。
 * 走 multipart（同图片/音频），字段名 `file`。返回引用键 + 原始名 + doc_status。
 */
export async function uploadDocument(file: File): Promise<ChatAttachment> {
  const form = new FormData();
  form.append("file", file);
  const res = await multipartFetch<{ key: string; name?: string; doc_status?: "received" | "parsing" }>(
    `${BASE}/uploads/documents`,
    form,
    { defaultErrorMessage: "文档上传失败", defaultErrorCode: "DOCUMENT_UPLOAD_ERROR" }
  );
  return { kind: "document", ref: res.key, name: res.name ?? file.name, doc_status: res.doc_status ?? "received" };
}
