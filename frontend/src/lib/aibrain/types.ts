// 华鼎AI智脑 · 前端一期类型 + 契约常量（AIBRAIN-UI-0001，mock 先行）。
//
// 🔴 本文件是 FE 侧对 BE 增量 1 契约的**镜像**。字段命名/枚举/数值以「需求冻结 §决策定稿 D1-D7」为准，
// BE 合并后逐字段复核（见回执「契约对齐」）。放在独立 `lib/aibrain/` 目录，不碰共享 types.ts（避让电商线 §七）。

/** 「智能强度」三档（D2，已按成本纠正）。**前端只传档位、不传裸模型名**（服务端固定 allowlist）。 */
export type IntensityTier = "low" | "mid" | "high";

/**
 * 一档的展示 + 预检元信息。
 * - `reserve`：开答前**预留上限**（D4「预留上限」）——FE 拿它做**余额预检**的快速第一道（BE 才是权威）。
 *   典型消耗（`typical`）是均值，reserve 是单轮可能的**上限**（≈ 最大输出 token 的成本），且受单次上限 200 封顶。
 * - `typical`：一次典型问答(500入+500出)的消耗（D3），用于选择器旁「让用户有预期」的文案。
 * - `model` 仅用于内部标注/tooltip，**不随请求发送**。
 */
export interface TierMeta {
  tier: IntensityTier;
  /** 中文档位名（低/中/高）。 */
  label: string;
  /** 内部模型标识（展示用，不发送）。 */
  model: string;
  /** 开答前预留上限（积分）——FE 余额预检用。 */
  reserve: number;
  /** 典型一次问答消耗（积分），选择器旁标注。 */
  typical: number;
}

/** 单次问答上限（D4：200 积分，约 luna 30 轮 / sol 6 轮）。 */
export const SINGLE_TURN_LIMIT = 200;

/** 一条附件的额外预留估计（token→积分粗估）。**mock 与 FE 预检共用同一个数**，故 mock 不可能比 FE 松。 */
export const ATTACH_RESERVE = 30;

/**
 * 三档定稿（D2 档位 + D3 典型消耗）。reserve 为 FE 预检的单轮上限估计（≤ SINGLE_TURN_LIMIT）。
 * 🔴 数值与 BE 增量 1 的 tier 配置须一致；BE 合并后以 BE 为准复核（本表是 mock 先行的镜像）。
 */
export const TIERS: Record<IntensityTier, TierMeta> = {
  low: { tier: "low", label: "低", model: "gpt-5.6-luna", reserve: 40, typical: 6 },
  mid: { tier: "mid", label: "中", model: "gpt-5.6-terra", reserve: 100, typical: 15 },
  high: { tier: "high", label: "高", model: "gpt-5.6-sol", reserve: SINGLE_TURN_LIMIT, typical: 30 }
};

export const TIER_ORDER: IntensityTier[] = ["low", "mid", "high"];

/** 充值档位（D4：100 / 500 / 1000 / 2000 积分）。 */
export const RECHARGE_TIERS: number[] = [100, 500, 1000, 2000];

/** 附件类型（一期：图片走既有 /uploads；文档 pdf/docx/txt 一期只收不解析——D6，解析在 BE 增量 3）。 */
export type AttachmentKind = "image" | "document";

/** 一条消息里带的附件（提交时只传引用键，不传 bytes）。 */
export interface ChatAttachment {
  kind: AttachmentKind;
  /** 图片 = /uploads 返回的 key；文档 = 文档上传返回的 key/asset_id。 */
  ref: string;
  /** 原始文件名（展示用）。 */
  name: string;
  /** 图片缩略/预览 URL（文档无）。 */
  preview_url?: string | null;
  /** 文档一期状态：received=已收到、parsing=解析中（**不假装已解析**，D6/任务包 §4）。 */
  doc_status?: "received" | "parsing" | null;
}

export type MessageRole = "user" | "assistant";

/**
 * 一条消息。**渲染要为「增量追加」留口**（任务包 §2 / D3 流式在 BE 增量 2）：
 * 一期非流式 `status` 恒 `complete`；增量 2 流式时组件只需把 chunk 追加到 `content`、`status:"streaming"`，
 * 不必二次重写。故 content 始终是单一可追加文本节点。
 */
export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  status: "complete" | "streaming" | "failed";
  attachments?: ChatAttachment[];
  /** 助手消息答完后的实扣积分（D4 按 token 实扣，多退少补）；用户消息为 null。 */
  cost_credits?: number | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationListResponse {
  items: Conversation[];
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
}

/** 推理积分钱包（D4：独立表 reasoning_wallets 的 FE 视图）。 */
export interface ReasoningWallet {
  /** 当前推理积分余额。 */
  balance: number;
  /** 单次问答上限（服务端下发，FE 不硬编码——但有 SINGLE_TURN_LIMIT 兜底常量）。 */
  single_turn_limit: number;
}

/** 发消息请求体。**tier 必传**（承重：档位随请求传）。 */
export interface SendMessageRequest {
  content: string;
  tier: IntensityTier;
  attachments?: ChatAttachment[];
}

/** 发消息响应（一期非流式：一次返完整助手消息 + 结算后的余额）。 */
export interface SendMessageResponse {
  user_message: ChatMessage;
  assistant_message: ChatMessage;
  /** 结算后的最新余额（D4 多退少补后的真实余额）。 */
  balance: number;
}

/** 充值请求/响应（余额 1:1 → 推理积分；单向不可退，D4）。 */
export interface RechargeRequest {
  amount: number;
}
export interface RechargeResponse {
  balance: number;
}

/**
 * 🔴 可辨识错误码（与 BE 增量 1 对齐；FE 靠 code 而非 message 分流）。
 * - 余额不足 → 不是普通报错，是**弹充值窗**（任务包 §3）。
 * - 超单次上限 → friendly 提示（任务包 §3）。
 */
export const AIBRAIN_ERROR = {
  INSUFFICIENT_BALANCE: "INSUFFICIENT_BALANCE",
  OVER_SINGLE_LIMIT: "OVER_SINGLE_LIMIT",
  INVALID_TIER: "INVALID_TIER",
  CONVERSATION_NOT_FOUND: "CONVERSATION_NOT_FOUND"
} as const;

/** 单轮开答前的预留上限（档位基线 + 附件加成）。**mock 与 FE 预检共用**，保证同一口径。 */
export function reserveFor(tier: IntensityTier, attachments: ChatAttachment[] = []): number {
  return TIERS[tier].reserve + attachments.length * ATTACH_RESERVE;
}

export type SendPrecheck =
  | { ok: true; reserve: number }
  | { ok: false; reason: "over_limit" | "insufficient"; reserve: number };

/**
 * 🔴 发送前预检（承重的核心）——**拦在开答前**（D4）：
 *  - reserve 超单次上限 → `over_limit`（friendly 提示，不发请求）；
 *  - 余额 < reserve → `insufficient`（**弹充值窗、不发请求**，任务包 §3/§6）。
 * 这是快速第一道（BE 才是权威），但它决定「发不发请求」——去掉它，「余额不足不发请求」承重必红。
 */
export function precheckSend(balance: number, tier: IntensityTier, attachments: ChatAttachment[] = []): SendPrecheck {
  const reserve = reserveFor(tier, attachments);
  if (reserve > SINGLE_TURN_LIMIT) return { ok: false, reason: "over_limit", reserve };
  if (balance < reserve) return { ok: false, reason: "insufficient", reserve };
  return { ok: true, reserve };
}
