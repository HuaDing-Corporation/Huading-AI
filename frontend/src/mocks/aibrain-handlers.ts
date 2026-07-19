// 华鼎AI智脑 · MSW mock（AIBRAIN-UI-0001，mock 先行，对齐 BE 增量 1）。
//
// 🔴 mock **不能比 BE 宽松**（本项目多次教训）——正确拒绝：非法档位 / 余额不足 / 超单次上限 / 未知会话。
// 复用 FE 侧的契约常量（TIERS/SINGLE_TURN_LIMIT/RECHARGE_TIERS）→ mock 与 UI 同一套数值，结构上不可能漂松。
// 作为独立文件 `...aibrainHandlers()` 追加进 handlers.ts 末尾（避开电商段落 §七）。
//
// ⚠️ 跨租户：本项目 mock 从 token 派生**单租户**，「跨租户在 mock 里不可表达」（handlers.ts 既有诚实注释）。
//   故这里对「访问不属于本租户的会话」= store 查不到 = 404，**不伪造 tenant 自比**（那是装饰不是防线）。BE 才是权威。

import { http, HttpResponse } from "msw";

import {
  AIBRAIN_ERROR,
  RECHARGE_TIERS,
  reserveFor,
  SINGLE_TURN_LIMIT,
  TIERS,
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

const SEED_BALANCE = 50; // 够低档(reserve 40)、不够中/高档 → 余额不足与充值路径都可测

interface MockConversation extends Conversation {
  messages: ChatMessage[];
}

const conversations = new Map<string, MockConversation>();
let convSeq = 0;
let msgSeq = 0;
let walletBalance = SEED_BALANCE;

/** 测试用重置（stores 是模块级、server.resetHandlers 不清；每个用例自行在 beforeEach 调）。 */
export function resetAibrain(): void {
  conversations.clear();
  convSeq = 0;
  msgSeq = 0;
  walletBalance = SEED_BALANCE;
}

function isTier(v: unknown): v is IntensityTier {
  return v === "low" || v === "mid" || v === "high";
}

/** 会话「元信息」视图（不含 messages）——列表/新建返回用。 */
function metaOf(conv: MockConversation): Conversation {
  return { id: conv.id, title: conv.title, created_at: conv.created_at, updated_at: conv.updated_at };
}

function newConversation(): MockConversation {
  const now = new Date(2026, 6, 19, 10, 0, convSeq).toISOString();
  const id = `conv-${++convSeq}`;
  return { id, title: "新对话", created_at: now, updated_at: now, messages: [] };
}

export function aibrainHandlers() {
  return [
    // ── 会话列表 / 新建 ──────────────────────────────────────────────
    http.get(`${BASE}/api/v1/aibrain/conversations`, () =>
      ok({
        items: [...conversations.values()].map(metaOf).sort((a, b) => b.updated_at.localeCompare(a.updated_at))
      })
    ),
    http.post(`${BASE}/api/v1/aibrain/conversations`, () => {
      const conv = newConversation();
      conversations.set(conv.id, conv);
      return ok(metaOf(conv), 201);
    }),

    // ── 会话详情 / 删除 ──────────────────────────────────────────────
    http.get(`${BASE}/api/v1/aibrain/conversations/:id`, ({ params }) => {
      const conv = conversations.get(String(params.id));
      if (!conv) return err(404, AIBRAIN_ERROR.CONVERSATION_NOT_FOUND, "会话不存在或无权访问");
      return ok(conv);
    }),
    http.delete(`${BASE}/api/v1/aibrain/conversations/:id`, ({ params }) => {
      const id = String(params.id);
      if (!conversations.has(id)) return err(404, AIBRAIN_ERROR.CONVERSATION_NOT_FOUND, "会话不存在或无权访问");
      conversations.delete(id);
      return ok({ id });
    }),

    // ── 发消息（非流式，reserve→settle）──────────────────────────────
    http.post(`${BASE}/api/v1/aibrain/conversations/:id/messages`, async ({ params, request }) => {
      const conv = conversations.get(String(params.id));
      if (!conv) return err(404, AIBRAIN_ERROR.CONVERSATION_NOT_FOUND, "会话不存在或无权访问");

      const body = (await request.json().catch(() => ({}))) as {
        content?: string;
        tier?: unknown;
        attachments?: ChatAttachment[];
      };
      // 🔴 非法档位（非 low/mid/high）→ 拒（不比 BE 宽松）。
      if (!isTier(body.tier)) return err(422, AIBRAIN_ERROR.INVALID_TIER, "智能强度档位非法（low/mid/high）");
      const content = (body.content ?? "").trim();
      const attachments = Array.isArray(body.attachments) ? body.attachments : [];
      if (!content && attachments.length === 0) return err(422, "VALIDATION_ERROR", "消息不能为空");

      const reserve = reserveFor(body.tier, attachments);
      // 🔴 超单次上限（D4：200 积分）→ friendly 拒。**先于余额检查**（与预留无关，是硬上限）。
      if (reserve > SINGLE_TURN_LIMIT)
        return err(422, AIBRAIN_ERROR.OVER_SINGLE_LIMIT, `单次问答预计消耗超过 ${SINGLE_TURN_LIMIT} 积分上限，请精简内容或附件`);
      // 🔴 余额不足（拦在开答前）→ 弹充值窗（FE 靠 code 分流）。
      if (walletBalance < reserve)
        return err(422, AIBRAIN_ERROR.INSUFFICIENT_BALANCE, "推理积分不足，请充值后再试");

      const now = new Date(2026, 6, 19, 10, 30, msgSeq).toISOString();
      const userMsg: ChatMessage = {
        id: `msg-${++msgSeq}`,
        role: "user",
        content,
        status: "complete",
        attachments: attachments.length ? attachments : undefined,
        cost_credits: null,
        created_at: now
      };
      // 结算：按典型消耗实扣（D4 多退少补——reserve 是上限，settle 是实际）。
      const cost = TIERS[body.tier].typical;
      walletBalance = Math.max(0, walletBalance - cost);
      const assistantMsg: ChatMessage = {
        id: `msg-${++msgSeq}`,
        role: "assistant",
        content: `（${TIERS[body.tier].label}档 · ${TIERS[body.tier].model}）已收到你的问题：「${content || "[附件]"}」。这是 mock 回答，联调后由真实模型作答。`,
        status: "complete",
        cost_credits: cost,
        created_at: new Date(2026, 6, 19, 10, 30, msgSeq).toISOString()
      };
      conv.messages.push(userMsg, assistantMsg);
      conv.updated_at = assistantMsg.created_at;
      if (conv.title === "新对话" && content) conv.title = content.slice(0, 20);

      return ok({ user_message: userMsg, assistant_message: assistantMsg, balance: walletBalance }, 201);
    }),

    // ── 钱包 / 充值 ──────────────────────────────────────────────────
    http.get(`${BASE}/api/v1/aibrain/wallet`, () =>
      ok({ balance: walletBalance, single_turn_limit: SINGLE_TURN_LIMIT })
    ),
    http.post(`${BASE}/api/v1/aibrain/wallet/recharge`, async ({ request }) => {
      const body = (await request.json().catch(() => ({}))) as { amount?: unknown };
      // 🔴 非法档位（非 100/500/1000/2000）→ 拒。
      if (typeof body.amount !== "number" || !RECHARGE_TIERS.includes(body.amount))
        return err(422, "VALIDATION_ERROR", "充值档位非法（100 / 500 / 1000 / 2000）");
      walletBalance += body.amount; // 1:1 充值；单向不可退（无退款端点）
      return ok({ balance: walletBalance });
    }),

    // ── 文档上传（一期只收下，mock 返回「已收到」；解析在 BE 增量 3，不假装已解析）──────────
    http.post(`${BASE}/api/v1/aibrain/uploads/documents`, async ({ request }) => {
      const form = await request.formData().catch(() => null);
      const file = form?.get("file");
      const name = file instanceof File ? file.name : "document";
      return ok({ key: `doc-mock-${name}`, name, doc_status: "received" as const }, 201);
    })
  ];
}
