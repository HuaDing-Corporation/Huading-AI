// 华鼎AI智脑 · 前端类型 + 契约常量（AIBRAIN-UI-0001 · FIX1：按 BE 增量 1 真实源码逐字段对齐）。
//
// 🔴 契约源 = BE `f2e9a2e0`（schemas/aibrain.py · routes/aibrain.py · services/aibrain.py）。**以 BE 为准**。
// 独立 `lib/aibrain/` 目录，不碰共享 types.ts（避让电商线）。

/** 「智能强度」三档（BE `_TIER_MODELS`）。**前端只传档位**（服务端固定 allowlist）。 */
export type IntensityTier = "low" | "mid" | "high";

/**
 * 一档的**计费费率**（积分 / 千 token），按输入、输出分别计价。
 *
 * 🔴 契约源 = BE `app/core/config.py` 的 `engine_aibrain_{low,mid,high}_{input,output}_credits_per_1k`
 *    （PR #239 `codex/pricing-c3c4-be`，config.py:211-216）。**BE 是唯一权威，此处是镜像**。
 * 🔴 为什么必须是费率、而不是一个「典型值」整数（PRICING-UI-0001 §二）：本文件此前写死
 *    `typical: 6/15/30`，那是**旧费率**下 500 输入 + 500 输出的估算值，可没有一个字说明这一点。
 *    BE 把费率降了 35%（1.73/10.37 → 1.12/6.72）之后，UI 仍然展示 6/15/30 —— 无人察觉，因为
 *    一个裸整数看不出它是怎么来的。改成费率之后，展示的每个数字都由 `typicalCredits()` /
 *    `minReservationCredits()` **从费率推导**，费率一改全部跟着走，且口径能对用户讲清楚。
 * ⚠️ 费率是**租户可覆写**的（BE `CreditRate` 表优先于 config 默认值）。此处的值是 config 默认值，
 *    仅用于「发送前给用户一个预期」；**实际扣费一律以 BE 返回的 `charged_credits` 为准**，前端不参与算账。
 */
export interface TierRate {
  /** 输入（prompt）积分 / 千 token。 */
  inputPer1k: number;
  /** 输出（completion）积分 / 千 token。 */
  outputPer1k: number;
}

/** 一档的展示元信息。价格只以 `rate` 表达——**不许再出现无说明的裸整数**。 */
export interface TierMeta {
  tier: IntensityTier;
  label: string;
  /** 内部模型标识（展示/ tooltip，不发送）。 */
  model: string;
  /** 该档费率（唯一价格真源）。 */
  rate: TierRate;
}

/**
 * @deprecated BE #239 起**预留额是动态的**（按提示词估算 × 1.25 + 完整 completion 配额），
 * 这个 flat 200 只因 `ReasoningWalletRead.single_request_limit` 字段还在响应里而保留
 * （BE `aibrain.py` 自陈：「Kept in the wallet response for backward compatibility; reservations are dynamic.」）。
 * 🔴 **不要拿它当「本次要花多少 / 最多花多少」展示给用户** —— 它两者都不是。
 */
export const SINGLE_REQUEST_LIMIT = 200;

/** BE 单次回答的 completion 上限（`engine_aibrain_max_completion_tokens`，config.py:225）。预留下界按它算。 */
export const MAX_COMPLETION_TOKENS = 4096;

/**
 * 「典型对话」的口径 —— 展示「约 N 积分/次」时**必须同时讲清这两个数**，否则又是一个裸整数。
 * 取值沿用旧 `typical` 6/15/30 的口径（500 输入 + 500 输出），使新旧展示可比。
 */
export const TYPICAL_PROMPT_TOKENS = 500;
export const TYPICAL_COMPLETION_TOKENS = 500;

/** 三档（模型标识 = BE services/aibrain.py `_TIER_MODELS`；费率见 `TierRate` 契约注释）。 */
export const TIERS: Record<IntensityTier, TierMeta> = {
  low: { tier: "low", label: "低", model: "gpt-5.6-luna", rate: { inputPer1k: 1.12, outputPer1k: 6.72 } },
  mid: { tier: "mid", label: "中", model: "gpt-5.6-terra", rate: { inputPer1k: 2.8, outputPer1k: 16.8 } },
  high: { tier: "high", label: "高", model: "gpt-5.6-sol", rate: { inputPer1k: 5.6, outputPer1k: 33.6 } }
};

/**
 * 典型一次问答的消耗（积分，取整用于「约 N 积分/次」）。
 * 现费率下 → low 3.92≈4 · mid 9.80≈10 · high 19.60≈20（旧费率下是 6.05/15.12/30.24，即老 UI 的 6/15/30）。
 * 🔴 展示这个数时**必须带口径**（`copy.aibrain.intensityRateHint`），不许单独出现。
 */
export function typicalCredits(tier: IntensityTier): number {
  const { inputPer1k, outputPer1k } = TIERS[tier].rate;
  return Math.round((TYPICAL_PROMPT_TOKENS * inputPer1k + TYPICAL_COMPLETION_TOKENS * outputPer1k) / 1000);
}

/**
 * 本次请求**至少**会被临时预留多少积分 —— 即「完整 completion 配额」那一段
 * （BE `_reservation_credits` = `_user_credits(ceil(提示词估算 × 1.25), max_completion_tokens)`）。
 *
 * 🔴 只算 completion 段、并对用户明说「至少」：completion 段 = `4096 × 输出费率`，是**纯常量 × 费率**，
 *    不依赖任何 token 估算，前端算得出且不会错。而 prompt 段要复刻 BE 的 `_estimate_text_tokens`
 *    （ASCII 四字符一 token、非 ASCII 一字一 token）**加上最近 20 轮上下文的拼装**才能得到 —— 前端
 *    复刻它必然与 BE 漂移，算错了显示给用户比不显示更糟。故此处给**可验证的下界**，不猜完整值。
 * 现费率下 → low 27.5 · mid 68.8 · high 137.6。
 */
export function minReservationCredits(tier: IntensityTier): number {
  return (TIERS[tier].rate.outputPer1k * MAX_COMPLETION_TOKENS) / 1000;
}

/**
 * 积分展示格式：最多 1 位小数、整数不带 `.0`（137.6256 → "137.6"，27.52 → "27.5"，20 → "20"）。
 * 单点存在是为了让「同一个数在弹窗里和提示里长得一样」，也让格式本身可被测试钉住。
 */
export function formatCredits(credits: number): string {
  return credits.toFixed(1).replace(/\.0$/, "");
}

/** 费率展示格式：固定 2 位小数（1.12 / 6.72）——费率本来就是两位定价，抹掉末位会让人以为是 1.1。 */
export function formatRate(rate: number): string {
  return rate.toFixed(2);
}

export const TIER_ORDER: IntensityTier[] = ["low", "mid", "high"];

/** 充值档位（BE `TopupAmount = Literal[100,500,1000,2000]`；钱包 `topup_options` 亦下发同值）。 */
export const TOPUP_OPTIONS: number[] = [100, 500, 1000, 2000];

/**
 * 响应里的附件（BE `ChatAttachmentRead`）。
 * 🔴 FIX2：BE FIX1 补了 `download_url`（`_attachment_download_urls`：SQL 租户过滤 + presign + 只签 image）→
 * 历史图片附件现在能显**真缩略图**（`download_url` 为 null 时降级为占位片）。
 */
export interface ChatAttachment {
  asset_id: string;
  asset_type: string;
  mime_type: string;
  download_url?: string | null;
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
  // ⚠️ PR #239 起 BE **不再发这个码**（`_max_completion_tokens` 连同那条 422 一起被删——预留改为动态、
  //    答不下就追加预留而不是拒绝）。分流保留：#239 上线前的 BE 仍会发，删掉会让那段时间漏分流；
  //    #239 之后它永不触发，留着无害。确认全环境升级完毕后可摘。
  REQUEST_LIMIT_EXCEEDED: "AIBRAIN_REQUEST_LIMIT_EXCEEDED", // 422（#239 起废止）
  // 🔴 PR #239 新增：答完要**追加预留**时发现这条消息已不是 pending（并发/超时回收）。
  //    BE `aibrain.py:_expand_reasoning_reservation`。
  REQUEST_EXPIRED: "AIBRAIN_REQUEST_EXPIRED", // 409
  CONVERSATION_NOT_FOUND: "AIBRAIN_CONVERSATION_NOT_FOUND", // 404
  ATTACHMENT_NOT_FOUND: "AIBRAIN_ATTACHMENT_NOT_FOUND", // 404
  ATTACHMENT_INVALID: "AIBRAIN_ATTACHMENT_INVALID", // 422
  PROVIDER_FAILED: "AIBRAIN_PROVIDER_FAILED", // 502
  // 🔴 FIX2：同 idempotency_key + 不同金额 → 409（正常流程不该触发——改档位就换新键——但触发了要看得懂）。
  IDEMPOTENCY_KEY_REUSED: "AIBRAIN_IDEMPOTENCY_KEY_REUSED" // 409
} as const;

export type SendPrecheck = { ok: true } | { ok: false; reason: "insufficient" };

/**
 * 🔴 发送前预检（承重核心）—— 只拦「**账上一分钱都没有**」这一种必然失败。
 *
 * BE 口径（PR #239 `_apply_reasoning_wallet_change` 的 reserve 分支）：`available < requested` → 402。
 * requested 是动态预留（提示词估算 × 1.25 + 完整 completion 配额），**前端算不出**（见
 * `minReservationCredits` 的注释：prompt 段要复刻 BE 的 token 估算 + 20 轮上下文拼装）。
 * 故前端唯一能**零误判**预判的仍是 `available <= 0`：此时任何正数 requested 都不满足，必 402。
 * ⚠️ `available > 0` 但不够预留的情形**故意放行**，由 BE 的 402 权威裁决 —— 前端拿下界去拦会把
 *    「上下文短、实际只需几十积分」的用户误锁在门外（宁可多一次往返，不可错杀）。
 * ⚠️ `available` 为 `undefined`（钱包**未加载/加载失败**）同样不预拦（CR#2），理由同上。
 */
export function precheckSend(availableCredits: number | undefined): SendPrecheck {
  if (typeof availableCredits === "number" && availableCredits <= 0) return { ok: false, reason: "insufficient" };
  return { ok: true };
}

/**
 * 402 余额不足时，向用户交代清楚的三个数（PRICING-UI-0001 §三 的第 1/2/3 条；第 4 条「临时预留、
 * 结束即退」是文案，见 `copy.aibrain.insufficientReserveNote`）。
 *
 * 🔴 **为什么不从 402 响应体里取 required / available**：BE 把这两个数**只写在英文 message 自由文本里**
 *    （"Insufficient reasoning balance for this request (required X, available Y)."），而
 *    `app_error_handler`（core/exceptions.py）转发 AppError 时**只传 code + message，detail 恒为 null**。
 *    要拿到就得正则解析那句英文 —— BE 一改措辞前端就静默退化成「什么都不显示」，且没有任何测试会红。
 *    本项目在价格上已经栽过一次这种静默漂移（本包 §二 修的就是它），不再栽第二次。
 *    → 已写进回执请 CA 在 402 里补结构化字段；**字段名定下来之前不写解析代码**（写了也永远不生效，
 *      却让人以为已经生效 —— 假绿）。
 * 🔴 故 `minRequired` 用**可验证的下界**（completion 段 = 4096 × 输出费率），文案配「至少」二字。
 */
export interface ShortfallView {
  /** 本次**至少**会被临时预留的积分（下界，见 `minReservationCredits`）。 */
  minRequired: number;
  /** 当前可用积分；钱包未加载/加载失败时为 undefined（**不填 0**，0 是一个会误导的谎）。 */
  available?: number;
  /**
   * 至少还差多少。仅当余额已知**且确实低于下界**时给出；
   * `available >= minRequired` 却仍被 402，说明缺口来自提示词那一段（上下文长 / 图片多）——
   * 此时差额前端算不出，给 undefined 让 UI 换一句话说，**不显示「还差 0 积分」这种荒谬值**。
   */
  shortfall?: number;
}

export function shortfallView(tier: IntensityTier, availableCredits: number | undefined): ShortfallView {
  const minRequired = minReservationCredits(tier);
  const available = typeof availableCredits === "number" ? availableCredits : undefined;
  const shortfall = available !== undefined && available < minRequired ? minRequired - available : undefined;
  return { minRequired, available, shortfall };
}
