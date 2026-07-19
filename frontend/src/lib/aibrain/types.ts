// 华鼎AI智脑 · 前端类型 + 契约常量（AIBRAIN-UI-0001 · FIX1：按 BE 增量 1 真实源码逐字段对齐）。
//
// 🔴 契约源 = BE `f2e9a2e0`（schemas/aibrain.py · routes/aibrain.py · services/aibrain.py）。**以 BE 为准**。
// 独立 `lib/aibrain/` 目录，不碰共享 types.ts（避让电商线）。

/** 「智能强度」三档（BE `_TIER_MODELS`）。**前端只传档位**（服务端固定 allowlist）。 */
export type IntensityTier = "low" | "mid" | "high";

/** 一档的展示元信息。`typical` 仅**展示**（选择器旁「让用户有预期」），**不是预留额**（预留是 BE 的 flat 200）。 */
export interface TierMeta {
  tier: IntensityTier;
  label: string;
  /** 内部模型标识（展示/ tooltip，不发送）。 */
  model: string;
  /** 典型一次问答消耗（积分，D3）——展示用。 */
  typical: number;
}

/** 单次问答预留上限（BE `_SINGLE_REQUEST_LIMIT = 200`，不分档位、flat）。 */
export const SINGLE_REQUEST_LIMIT = 200;

/** 三档（BE services/aibrain.py `_TIER_MODELS`；typical 取 D3 展示值）。 */
export const TIERS: Record<IntensityTier, TierMeta> = {
  low: { tier: "low", label: "低", model: "gpt-5.6-luna", typical: 6 },
  mid: { tier: "mid", label: "中", model: "gpt-5.6-terra", typical: 15 },
  high: { tier: "high", label: "高", model: "gpt-5.6-sol", typical: 30 }
};

export const TIER_ORDER: IntensityTier[] = ["low", "mid", "high"];

/** 充值档位（BE `TopupAmount = Literal[100,500,1000,2000]`；钱包 `topup_options` 亦下发同值）。 */
export const TOPUP_OPTIONS: number[] = [100, 500, 1000, 2000];

/** 响应里的附件（BE `ChatAttachmentRead`）——**只有 asset_id/asset_type/mime_type，无 URL**。 */
export interface ChatAttachment {
  asset_id: string;
  asset_type: string;
  mime_type: string;
}

/** 客户端待发附件（组件本地态：图片 asset_id + 本地预览）。发送时只提取 asset_id 进 attachment_asset_ids。 */
export interface PendingAttachment {
  asset_id: string;
  name: string;
  /** 本地 objectURL 预览（响应无 URL，故仅用户刚上传的这张能预览）。 */
  preview_url: string;
}

export type MessageRole = "user" | "assistant";

/**
 * 一条消息（BE `ChatMessageRead`）。status = pending|completed|failed（**不是 complete/streaming**）。
 * 一期非流式：助手消息答完即 `completed`。渲染仍把 content 当**单一可追加文本节点**，为增量 2 流式留口
 * （届时 pending 期间逐 chunk 追加进 content 即可，不必重写）。
 */
export interface ChatMessage {
  id: string;
  conversation_id: string;
  role: MessageRole;
  content: string;
  attachments: ChatAttachment[];
  tier?: IntensityTier | null;
  model?: string | null;
  status: "pending" | "completed" | "failed";
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  reserved_credits?: number;
  /** 实扣积分（BE `charged_credits`，多退少补后）。 */
  charged_credits?: number;
  created_at: string;
}

/** 会话摘要（BE `ConversationSummary`）。 */
export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

/** 会话列表（BE `ConversationListResponse` = {items, total}）。 */
export interface ConversationListResponse {
  items: Conversation[];
  total: number;
}

/** 会话详情（BE `ConversationRead` = summary + messages）。 */
export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
}

/** 推理积分钱包（BE `ReasoningWalletRead`）。余额 = `available_credits`（**不是 balance**）。 */
export interface ReasoningWallet {
  available_credits: number;
  reserved_credits: number;
  total_topup_credits: number;
  total_spent_credits: number;
  topup_options: number[];
  single_request_limit: number;
}

/** 发消息请求（BE `ChatMessageCreateRequest`，`extra=forbid`）。**附件是 asset_id 列表**（max 10，唯一，非空）。 */
export interface SendMessageRequest {
  content: string;
  tier: IntensityTier;
  attachment_asset_ids: string[];
}

/** 发消息响应（BE `ChatMessageCreateResponse`）——含 **`wallet`**（不是 balance）。 */
export interface SendMessageResponse {
  user_message: ChatMessage;
  assistant_message: ChatMessage;
  wallet: ReasoningWallet;
}

/**
 * 充值请求（BE `POST /wallet/topup` → `ReasoningWalletRead`）。单向不可退。
 * 🔴 `idempotency_key`（FIX1 · §四之二）：推理积分不可退 → 一次网络重试的双扣是**不可逆**的。故充值必带幂等键，
 * 服务端对同一 key 重放**返回首次结果、不产生第二笔**。**每次充值尝试生成一次、重试复用同一个、取消后重发=新 key**。
 * ⚠️ 字段名/形状最终以 BE FIX1 回执为准（先按 `idempotency_key` 实现，回执到再核）。
 */
export interface TopupRequest {
  amount: number;
  idempotency_key: string;
}

/**
 * 🔴 可辨识错误码（BE 全系 `AIBRAIN_` 前缀 + 真实 status）。FE 靠 code+status 分流：
 *  - 402 余额不足 → **弹充值窗**（不是普通报错）；
 *  - 422 超单次上限/请求过大 → friendly「精简内容」；
 *  - 502 上游失败 → friendly「服务暂不可用，请重试」。
 */
export const AIBRAIN_ERROR = {
  INSUFFICIENT_BALANCE: "AIBRAIN_INSUFFICIENT_BALANCE", // 402
  REQUEST_LIMIT_EXCEEDED: "AIBRAIN_REQUEST_LIMIT_EXCEEDED", // 422
  CONVERSATION_NOT_FOUND: "AIBRAIN_CONVERSATION_NOT_FOUND", // 404
  ATTACHMENT_NOT_FOUND: "AIBRAIN_ATTACHMENT_NOT_FOUND", // 404
  ATTACHMENT_INVALID: "AIBRAIN_ATTACHMENT_INVALID", // 422
  PROVIDER_FAILED: "AIBRAIN_PROVIDER_FAILED" // 502
} as const;

export type SendPrecheck = { ok: true } | { ok: false; reason: "insufficient" };

/**
 * 🔴 发送前预检（承重核心）——**对齐 BE 口径**（FIX1）：BE 预留 `min(200, available)`，`available<=0` → 402。
 * 故 FE 唯一能可靠预判的是「**有没有余额可预留**」：`available_credits <= 0` → 弹充值窗、**不发请求**。
 * ⚠️ `available` 为 `undefined`（钱包**未加载/加载失败**）时**不预拦**——否则会把有余额的用户也锁死（CR#2）；
 *    此时放行，由 BE 的 402 权威兜底（`AibrainChat` 收到再弹充值窗）。
 * 更细的「预留够不够买 token」需 tokenize + 定价，FE 算不了 → 交给 BE 的 422。
 * ⚠️ 不拿「预留额 200」当「本次消耗」展示给用户（那是瞬时锁再退回）——展示用 `TIERS[tier].typical`（6/15/30）。
 */
export function precheckSend(availableCredits: number | undefined): SendPrecheck {
  if (typeof availableCredits === "number" && availableCredits <= 0) return { ok: false, reason: "insufficient" };
  return { ok: true };
}
