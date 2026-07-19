// 华鼎AI智脑 · MSW mock（AIBRAIN-UI-0001 · FIX1：逐字段镜像 BE f2e9a2e0，**不比 BE 宽松**，含 402/422/502 状态码本身）。
//
// 对齐点（以 BE 源码为准）：/wallet/topup（非 recharge）· 钱包 available_credits/... · 发消息体 attachment_asset_ids ·
// 响应含 wallet（非 balance）· 预留 flat min(200,available)：available<=0→402 AIBRAIN_INSUFFICIENT_BALANCE、
// 预留不够→422 AIBRAIN_REQUEST_LIMIT_EXCEEDED · 会话 404 AIBRAIN_CONVERSATION_NOT_FOUND。
// ⚠️ BE 增量 1 无 DELETE 会话、无文档上传（增量 3）→ 本 mock 不提供这两个端点（一期路径不调用）。
// ⚠️ 跨租户在 mock 不可表达（单租户 token）→ store 查不到 = 404，不伪造 tenant 自比（本项目既有诚实做法）。

import { http, HttpResponse } from "msw";

import {
  AIBRAIN_ERROR,
  SINGLE_REQUEST_LIMIT,
  TIERS,
  TOPUP_OPTIONS,
  type ChatAttachment,
  type ChatMessage,
  type Conversation,
  type IntensityTier
} from "@/lib/aibrain/types";

const BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const ok = <T>(data: T, status = 200) =>
  HttpResponse.json({ data, error: null, request_id: "mock-req" }, { status });
const err = (status: number, code: string, message: string) =>
  HttpResponse.json({ data: null, error: { code, message, request_id: "mock-req" }, request_id: "mock-req" }, { status });

/** content 含此串 → 模拟上游失败（测 502 分流）。 */
const PROVIDER_FAIL_MARKER = "__mock_provider_fail__";
/** 超长 content → 模拟「预留 200 也不够的大请求」→ 422（BE：max_completion_tokens<=0）。 */
const OVERSIZED_CONTENT = 8000;

interface MockConversation extends Conversation {
  messages: ChatMessage[];
}

const conversations = new Map<string, MockConversation>();
let convSeq = 0;
let msgSeq = 0;
// 钱包（BE 新租户从 0 起）。reserved 在同步 handler 里 reserve+settle 原子完成，故读时恒 0。
let available = 0;
let totalTopup = 0;
let totalSpent = 0;
// 充值幂等（§四之二 + FIX2）：key → {首次金额, 首次结果}。同 key+同额 → 返首次；同 key+异额 → 409（对齐 BE FIX1）。
const topupIdempotency = new Map<string, { amount: number; result: ReturnType<typeof walletView> }>();
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** 测试重置（stores 模块级，server.resetHandlers 不清）。 */
export function resetAibrain(): void {
  conversations.clear();
  convSeq = 0;
  msgSeq = 0;
  available = 0;
  totalTopup = 0;
  totalSpent = 0;
  topupIdempotency.clear();
}

function isTier(v: unknown): v is IntensityTier {
  return v === "low" || v === "mid" || v === "high";
}

/** BE 全系 `extra="forbid"` → 多传字段即 422。mock 同样拒，才不比 BE 宽松（CR#4）。 */
function hasExtraKeys(body: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(body).some((k) => !allowed.includes(k));
}

function walletView() {
  return {
    available_credits: available,
    reserved_credits: 0,
    total_topup_credits: totalTopup,
    total_spent_credits: totalSpent,
    topup_options: TOPUP_OPTIONS,
    single_request_limit: SINGLE_REQUEST_LIMIT
  };
}

function metaOf(conv: MockConversation): Conversation {
  return { id: conv.id, title: conv.title, created_at: conv.created_at, updated_at: conv.updated_at };
}

function newConversation(): MockConversation {
  const now = new Date(2026, 6, 19, 10, 0, convSeq).toISOString();
  const id = `conv-${++convSeq}`;
  return { id, title: "新对话", created_at: now, updated_at: now, messages: [] };
}

function attachmentsFrom(ids: string[]): ChatAttachment[] {
  // FIX2：BE 补了 download_url（presign，只签 image）——mock 给个可渲染的占位 URL，让前端走真缩略图路径。
  return ids.map((asset_id) => ({
    asset_id,
    asset_type: "generated_image",
    mime_type: "image/png",
    download_url: `https://mock.local/aibrain/${asset_id}.png`
  }));
}

export function aibrainHandlers() {
  return [
    // ── 钱包 / 充值（topup）───────────────────────────────────────────
    http.get(`${BASE}/api/v1/aibrain/wallet`, () => ok(walletView())),
    http.post(`${BASE}/api/v1/aibrain/wallet/topup`, async ({ request }) => {
      const body = (await request.json().catch(() => ({}))) as Record<string, unknown> & {
        amount?: unknown;
        idempotency_key?: unknown;
      };
      if (hasExtraKeys(body, ["amount", "idempotency_key"])) return err(422, "VALIDATION_ERROR", "extra fields forbidden");
      // idempotency_key 是 BE 必填 UUID（无默认）→ 缺失/非 UUID 即 422（不比 BE 宽松）。
      const key = typeof body.idempotency_key === "string" ? body.idempotency_key : "";
      if (!UUID_RE.test(key)) return err(422, "VALIDATION_ERROR", "idempotency_key 缺失或非法（须为 UUID）");
      if (typeof body.amount !== "number" || !TOPUP_OPTIONS.includes(body.amount))
        return err(422, "VALIDATION_ERROR", "充值档位非法（100 / 500 / 1000 / 2000）");
      // 🔴 幂等：同 key + **同额** → 返首次结果、不再加钱；同 key + **异额** → 409（对齐 BE FIX1）。
      const prev = topupIdempotency.get(key);
      if (prev) {
        if (prev.amount !== body.amount)
          return err(409, AIBRAIN_ERROR.IDEMPOTENCY_KEY_REUSED, "该幂等键已用于不同金额的充值");
        return ok(prev.result);
      }
      available += body.amount; // 1:1；单向不可退（无退款端点）
      totalTopup += body.amount;
      const result = walletView();
      topupIdempotency.set(key, { amount: body.amount, result });
      return ok(result);
    }),

    // ── 会话列表 / 新建 ──────────────────────────────────────────────
    http.get(`${BASE}/api/v1/aibrain/conversations`, () => {
      const items = [...conversations.values()].map(metaOf).sort((a, b) => b.updated_at.localeCompare(a.updated_at));
      return ok({ items, total: items.length });
    }),
    http.post(`${BASE}/api/v1/aibrain/conversations`, async ({ request }) => {
      const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
      if (hasExtraKeys(body, ["title"])) return err(422, "VALIDATION_ERROR", "extra fields forbidden");
      const conv = newConversation();
      conversations.set(conv.id, conv);
      return ok({ ...metaOf(conv), messages: [] }, 201);
    }),

    // ── 会话详情 ─────────────────────────────────────────────────────
    http.get(`${BASE}/api/v1/aibrain/conversations/:id`, ({ params }) => {
      const conv = conversations.get(String(params.id));
      if (!conv) return err(404, AIBRAIN_ERROR.CONVERSATION_NOT_FOUND, "会话不存在或无权访问");
      return ok({ ...metaOf(conv), messages: conv.messages });
    }),

    // ── 发消息（非流式，reserve→settle）──────────────────────────────
    http.post(`${BASE}/api/v1/aibrain/conversations/:id/messages`, async ({ params, request }) => {
      const conv = conversations.get(String(params.id));
      if (!conv) return err(404, AIBRAIN_ERROR.CONVERSATION_NOT_FOUND, "会话不存在或无权访问");

      const body = (await request.json().catch(() => ({}))) as Record<string, unknown> & {
        content?: string;
        tier?: unknown;
        attachment_asset_ids?: unknown;
      };
      if (hasExtraKeys(body, ["content", "tier", "attachment_asset_ids"]))
        return err(422, "VALIDATION_ERROR", "extra fields forbidden");
      // 档位（BE Literal → 422 校验错）。
      if (!isTier(body.tier)) return err(422, "VALIDATION_ERROR", "智能强度档位非法（low/mid/high）");
      const content = (body.content ?? "").trim();
      const ids = Array.isArray(body.attachment_asset_ids) ? (body.attachment_asset_ids as string[]) : [];
      // 附件校验（BE：max 10、唯一、非空）。
      if (ids.length > 10 || ids.some((x) => typeof x !== "string" || !x.trim()) || new Set(ids).size !== ids.length)
        return err(422, "VALIDATION_ERROR", "attachment_asset_ids 非法（≤10、唯一、非空）");
      if (!content && ids.length === 0) return err(422, "VALIDATION_ERROR", "消息不能为空");

      // 🔴 预留：flat min(200, available)。available<=0 → 402（BE：reserve<=0）。
      if (available <= 0) return err(402, AIBRAIN_ERROR.INSUFFICIENT_BALANCE, "推理积分不足，请充值后再试");
      const reservation = Math.min(SINGLE_REQUEST_LIMIT, available);
      // 🔴 FIX2 · 次序对齐 BE：预留不够买最小答复 → 422，**先于** provider 调用（BE aibrain.py:138-144 rollback+422）。
      if (content.length > OVERSIZED_CONTENT || reservation < TIERS[body.tier].typical)
        return err(422, AIBRAIN_ERROR.REQUEST_LIMIT_EXCEEDED, `单次问答超过 ${SINGLE_REQUEST_LIMIT} 积分上限或余额不足以作答，请精简内容或充值`);
      // 上游失败（测 502 分流）——在 422 之后（只有实际调用了 provider 才可能 502）。
      if (content.includes(PROVIDER_FAIL_MARKER))
        return err(502, AIBRAIN_ERROR.PROVIDER_FAILED, "AI 服务暂时不可用，请稍后重试");

      // 结算：min(实耗, 预留)。实耗用典型消耗；退回未用（available 只减实扣）。
      const charged = Math.min(TIERS[body.tier].typical, reservation);
      available -= charged;
      totalSpent += charged;

      const attachments = attachmentsFrom(ids);
      const now = new Date(2026, 6, 19, 10, 30, msgSeq).toISOString();
      const userMsg: ChatMessage = {
        id: `msg-${++msgSeq}`,
        conversation_id: conv.id,
        role: "user",
        content,
        attachments,
        tier: body.tier,
        status: "completed",
        created_at: now
      };
      const assistantMsg: ChatMessage = {
        id: `msg-${++msgSeq}`,
        conversation_id: conv.id,
        role: "assistant",
        content: `（${TIERS[body.tier].label}档 · ${TIERS[body.tier].model}）已收到：「${content || "[图片]"}」。这是 mock 回答，联调后由真实模型作答。`,
        attachments: [],
        tier: body.tier,
        model: TIERS[body.tier].model,
        status: "completed",
        charged_credits: charged,
        created_at: new Date(2026, 6, 19, 10, 30, msgSeq).toISOString()
      };
      conv.messages.push(userMsg, assistantMsg);
      conv.updated_at = assistantMsg.created_at;
      if (conv.title === "新对话" && content) conv.title = content.slice(0, 20);

      return ok({ user_message: userMsg, assistant_message: assistantMsg, wallet: walletView() });
    })
  ];
}
