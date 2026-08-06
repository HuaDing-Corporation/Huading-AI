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
export interface TierRateBand {
  /** 输入（prompt）积分 / 千 token。 */
  inputPer1k: number;
  /** 输出（completion）积分 / 千 token。 */
  outputPer1k: number;
}

/**
 * 🔴 PRICING-UI-0002：**一档 = 两个区间**，按本次请求的 prompt token 数选。
 *
 * 做成「一个对象里装两个区间 + 一个选择函数」而**不是**「两个独立 TierRate 让调用方挑」——
 * 后者等于把档位判据（`> 272,000`）复制到每个调用点，迟早有人漏掉一处，而漏掉的表现就是
 * 「显示 1.12、实扣 2.24」。判据只允许存在于 `rateForPromptTokens` 一个地方。
 */
export interface TierRate {
  /** ≤ 阈值（绝大多数会话走这一档）。 */
  standard: TierRateBand;
  /** > 阈值。 */
  extended: TierRateBand;
}

/**
 * 区间阈值：**prompt tokens 严格大于**这个数才进高区间（BE `_TokenRateTier.max_input_tokens`
 * 语义：`up_to_272k` 的上界是 272_000，超过才落到 `above_272k`）。
 * 🔴 显式命名常量，不许把 272000 散落在判断里 —— 那正是「魔数漂移」的经典形态。
 */
export const PROMPT_RATE_TIER_THRESHOLD_TOKENS = 272_000;

/**
 * 🔴 **档位判据的唯一实现**。任何需要费率的地方都走这里，不许自己拿 prompt 数跟阈值比。
 * 对齐 BE 的 `apimart_token_rate(model, prompt_tokens)`（售价侧与成本侧复用同一判据）。
 */
export function rateForPromptTokens(tier: IntensityTier, promptTokens: number): TierRateBand {
  const { standard, extended } = TIERS[tier].rate;
  return promptTokens > PROMPT_RATE_TIER_THRESHOLD_TOKENS ? extended : standard;
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

/**
 * 三档 × 两区间 = 十二格（模型标识 = BE services/aibrain.py `_TIER_MODELS`）。
 *
 * 🔴🔴 **`extended` 这六格目前没有售价侧源码可核，逐格标注在此**（承重要求：明确标注没查出来的）：
 *   · BE `config.py` 的 `engine_aibrain_*_credits_per_1k` **仍是单值**，没有 >272K 那一组；
 *   · 任务包说的分支 `codex/pricing-aibrain-tier` **在远端不存在**，develop 上也没有
 *     —— 即 `PRICING-AIBRAIN-TIER-0001` 这个后端包**尚未落地**。
 * ✅ 但这六个数**不是猜的**，有两重交叉验证：
 *   ① BE **成本侧**（`apimart_token_pricing.py`，已在 develop 上）的 272K 双区间比例逐档可核：
 *      luna 输入 8→16、输出 48→72；terra 20→40、120→180；sol 40→80、240→360
 *      → **三档一律「输入 ×2、输出 ×1.5」**；
 *   ② `standard` 六格乘上该比例，与任务包给的表格**十二格逐格相符**
 *      （1.12×2=2.24、6.72×1.5=10.08、2.8×2=5.6、16.8×1.5=25.2、5.6×2=11.2、33.6×1.5=50.4）。
 *   `PRICING-AIBRAIN-TIER-0001` 的正确性判据是「每档两区间加价率相等」，×2/×1.5 正满足它。
 * 🔴 **注意比例不是整体翻倍**：输入 ×2、输出 **×1.5**。照「双倍」写会把输出多算 33%。
 *    这一点有专门的门钉住（pricing.test.ts「十二格 / 比例」组）。
 * ⚠️ 售价侧落地后请回来逐格核对；`standard` 六格来自 #239 `config.py:211-216`（已核）。
 */
export const TIERS: Record<IntensityTier, TierMeta> = {
  low: {
    tier: "low",
    label: "低",
    model: "gpt-5.6-luna",
    rate: {
      standard: { inputPer1k: 1.12, outputPer1k: 6.72 },
      extended: { inputPer1k: 2.24, outputPer1k: 10.08 }
    }
  },
  mid: {
    tier: "mid",
    label: "中",
    model: "gpt-5.6-terra",
    rate: {
      standard: { inputPer1k: 2.8, outputPer1k: 16.8 },
      extended: { inputPer1k: 5.6, outputPer1k: 25.2 }
    }
  },
  high: {
    tier: "high",
    label: "高",
    model: "gpt-5.6-sol",
    rate: {
      standard: { inputPer1k: 5.6, outputPer1k: 33.6 },
      extended: { inputPer1k: 11.2, outputPer1k: 50.4 }
    }
  }
};

/**
 * 典型一次问答的消耗（积分，取整用于「约 N 积分/次」）。
 * 现费率下 → low 3.92≈4 · mid 9.80≈10 · high 19.60≈20（旧费率下是 6.05/15.12/30.24，即老 UI 的 6/15/30）。
 * 🔴 展示这个数时**必须带口径**（`copy.aibrain.intensityRateHint`），不许单独出现。
 */
export function typicalCredits(tier: IntensityTier): number {
  // 🔴 走 `rateForPromptTokens` 而不是直接取 `standard`：区间选择只有一个判据，这里也不例外。
  //    500 输入远小于 272,000 → 必落 `standard` → 扩区间**不改变**这三个值（4 / 10 / 20）。
  //    有专门的门钉住"没被意外改动"（pricing.test.ts 门2 + 十二格组）。
  const { inputPer1k, outputPer1k } = rateForPromptTokens(tier, TYPICAL_PROMPT_TOKENS);
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
  // 🔴 下界**取 `standard` 区间**（PRICING-UI-0002）：这个函数的语义是「**至少**会被预留多少」，
  //    而 `standard` 的输出费率恒低于 `extended`（×1.5）→ 用它才是真下界；用 `extended` 会高估，
  //    把一个上界说成「至少」是对用户报错价。
  //    真跑进 >272K 区间时实际预留会更高，那正是「至少」二字兜住的部分（文案本就带「至少」）。
  // ⚠️ 故此处**故意不走** `rateForPromptTokens` —— 它按「本次 prompt 数」选区间，而这里根本没有
  //    "本次"可言（下界是对所有请求成立的常量）。这不是漏了判据，是判据不适用。
  return (TIERS[tier].rate.standard.outputPer1k * MAX_COMPLETION_TOKENS) / 1000;
}

// ══ 积分展示：**两个语义明确的函数，不许混用**（FIX6 · P1-1）══════════════════════════════
//
// 🔴 演进史（三代，别照抄旧印象）：
//   ① 最初：固定 `toFixed(1)` —— `0.02` 显示成 `"0"`，用户以为「不差」→ 充值充不够（FIX5 修）
//   ② FIX5：逐级提升精度保证非零 —— 解决了"显示零"，但**没解决方向**：
//      `27.52512` 仍显示 `27.5`（低报"至少需要"）、`0.24192` 仍显示 `0.2`（低报实扣）
//   ③ FIX6（当前）：**按语义分叉** —— 一道门只能守它断言的那件事，一个函数也只能保证它规定的那个性质。
//      「不显示零」和「方向正确」是两个性质，得由两个规格分别保证。
//
// 🔴 **选哪个的判据**（写在这里，免得下一个人靠猜）：
//      这个数字回答的是「**你还得再拿出多少**」→ `formatCreditsUp`（向上，宁可多说）
//      这个数字回答的是「**实际发生/现在有多少**」→ `formatCreditsExact`（如实，六位去尾零）
//   两者都继承 FIX5 的判据：**非零金额不得显示为零**。
//
// ⚠️ 原来的 `formatCredits` **已删除**，不是改名。留一个语义含糊的通用函数，
//    等于把"选哪一类"这个判断继续留给每个调用点去猜 —— 而这正是 CB 判「分叉收益不足不成立」的理由。
/**
 * ① **缺口 / 预留 / 待补金额** —— `shortfall` · `required` · `minReservation` · `outstanding`。
 *
 * 规则：**向上取整**到 1 位小数 → 显示值恒 **≥** 真实值。
 * 🔴 为什么必须向上（FIX6 · CB）：这类数字回答的是「**你还得再拿出多少**」。
 *    低报的后果是**用户照着充值仍然发不出去** —— `27.52512` 显示成 `27.5`，他充 27.5，再发还是 402。
 *    FIX5 的实现只保证了"不显示零"，**没保证方向**：一道门只守它断言的那件事。
 * ⚠️ 只用于**非负**的缺口量。负数在这个语义下无意义（欠费的"当前余额"是余额类，走 `formatCreditsExact`）。
 * ⚠️ `credits * 10` 先 `toFixed(6)` 消浮点噪声再 `ceil`：否则 `1.1 * 10 = 11.000000000000002`
 *    会被 ceil 成 12 → 显示 `1.2`，凭空多报 0.1。
 */
export function formatCreditsUp(credits: number): string {
  if (credits === 0) return "0";
  const scaled = Number((credits * 10).toFixed(6));
  const text = (Math.ceil(scaled) / 10).toFixed(1).replace(/\.0$/, "");
  // 任何正的缺口向上取整必然 ≥ 0.1，到不了 "0"；负数按原样兜底（语义上不该出现）。
  return Number(text) !== 0 ? text : formatCreditsExact(credits);
}

/**
 * ② **实扣 / 可用余额** —— `message-bubble` 的本次消耗 · `wallet-balance` 的余额 · 402 里的 `available`
 *    · 欠费路径的「当前余额」。
 *
 * 规则：**按后端精度如实展示**（六位，去尾零）。
 * 🔴 为什么不能舍入（FIX6 · CB）：这类数字回答的是「**实际发生了什么**」。`0.24192` 显示成 `0.2`
 *    是**低报真实扣费**；钱包余额被舍入则等于不再展示后端返回的准确值。
 *    BE 把积分量化到 `Decimal("0.000001")`，六位正好是它的全部精度，不多不少。
 * ⚠️ 末行的兜底：比量化精度还小的值（契约坏了 / 前端自算）**仍不许显示为零**
 *    —— FIX5 立的那条「非零不得显示为零」判据在这里继续有效。
 */
export function formatCreditsExact(credits: number): string {
  if (credits === 0) return "0";
  const text = credits.toFixed(6).replace(/\.?0+$/, "");
  if (Number(text) !== 0) return text;
  return credits > 0 ? "0.000001" : "-0.000001";
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
  INSUFFICIENT_BALANCE: "AIBRAIN_INSUFFICIENT_BALANCE", // 402 · 预留不足（充值即可）
  // 🔴 PR #239 `e2bc2c02` 新增：**欠费**。与上一个是**两种不同情形**，文案必须能区分。
  //    产生路径（BE aibrain.py:250-266）：答案已经生成出来了（provider 的钱已经花了），此时才发现
  //    实扣超预留且追不上 → BE **照常交付答案**并按实际用量结算，允许钱包**变负**；随后
  //    reserve 分支最前面的 `available < 0` 闸门（:557）拦住一切新的付费请求，直到补齐。
  //    所以用户看到它时：上一次对话是**成功拿到答案**的，欠的是那一次的差额。
  OUTSTANDING_BALANCE: "AIBRAIN_OUTSTANDING_BALANCE", // 402 · 欠费（需先补齐）
  // 🔴🔴 FIX2 · `2a98b5d0` 新增：**租户在途敞口打满**。虽然也是 402，但它**不是余额问题**——
  //    上限 = `单请求最大敞口 × engine_aibrain_inflight_exposure_multiplier(默认 2)`，是 config 常量，
  //    与钱包余额毫无关系（BE `_tenant_inflight_exposure_limit`）。
  //    🔴 **充值不会解决它**：用户充再多钱，limit 也不会变大，必须等在途的请求答完（pending → 终态）。
  //    故本码**绝不许**走充值弹窗，见 aibrain-chat.tsx 的分流与 `copy.aibrain.inflightExposure*`。
  INFLIGHT_EXPOSURE_LIMIT: "AIBRAIN_INFLIGHT_EXPOSURE_LIMIT", // 402 · 在途太多（等待重试，充值无效）
  // FIX2 新增：提示词超过**本地**硬上限（BE `_prompt_token_upper_bound` > `engine_aibrain_max_prompt_tokens`）。
  // 发生在建消息、预留**之前**（aibrain.py:379）→ 未建消息、未动钱包。属于用户可自行解决的一类。
  PROMPT_LIMIT_EXCEEDED: "AIBRAIN_PROMPT_LIMIT_EXCEEDED", // 422
  // 🔴🔴 FIX3 · `fbe8420d` **契约变更**：FIX2 里接的 `AIBRAIN_PROVIDER_USAGE_LIMIT_EXCEEDED`
  //    （连同它那 7 个 detail 字段）**已被删除**，替换为下面这个 `..._USAGE_INVALID`，且**不带 detail**。
  //    同时「合法但超 envelope」不再报错 —— 改成 `min(reported, 上限)` **封顶扣费并正常交付**
  //    （aibrain.py:567-575）。所以现在这个 502 只在**上报本身不可信**时出现：
  //      · `_usage_contract_valid is False`（provider 用量契约校验没过）
  //      · `total_tokens != prompt + completion`（自相矛盾）
  //      · `total_tokens > 999_999_999`（超出 UsageRecord 可持久化范围）
  //      · 计价时抛 `APIMartTokenPricingError`（含溢出/越界，:1519-1551）
  PROVIDER_USAGE_INVALID: "AIBRAIN_PROVIDER_USAGE_INVALID", // 502
  // 🔴 FIX2 我从源码里捡到的那个码（任务包当时未列），FIX3/FIX4 仍在。
  USAGE_MISSING: "AIBRAIN_USAGE_MISSING", // 502
  // 🔴 FIX4 新增（第六个码，`b91e2188`）：**上游可能已经产生成本**却没给出可用结果。
  //    判据（aibrain.py:486-512）：provider 异常带 `request_may_have_been_accepted` /
  //    `has_cost_evidence`，或响应里有非零的 token/credits/cost 证据，或异常根本不是
  //    APIMart 类型（无从判断 → 保守当作已接单）。答案为空时同理（:628-632）。
  // ⚠️ 与 `PROVIDER_FAILED` 对用户**看起来是同一件事**（都没生成出来、都零扣费），但**后果不同**：
  //    本码在 `_USER_COOLDOWN_ERROR_CODES` 里（:82-86）→ **一定会开用户冷却**，立刻重试必撞 503。
  //    所以文案不许说「请重试」，见 `copy.aibrain.providerReplayGuard`。
  PROVIDER_REPLAY_GUARD: "AIBRAIN_PROVIDER_REPLAY_GUARD", // 502
  // 🔴 FIX3 新增（第五个码）：**用量异常冷却**。同租户在冷却窗口内出现过 USAGE_MISSING /
  //    USAGE_INVALID 的失败消息 → 新请求直接 503（aibrain.py:1293-1318，**判在最前**，
  //    比提示词闸和一切钱包闸都早）。窗口 = `engine_aibrain_usage_anomaly_cooldown_seconds`，
  //    默认 60s、范围 [1,300]。**无 detail、无 Retry-After 头**——见 copy 里对"约一分钟"的说明。
  PROVIDER_USAGE_ANOMALY_COOLDOWN: "AIBRAIN_PROVIDER_USAGE_ANOMALY_COOLDOWN", // 503
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

/** 预检拦下的两种情形——与 BE 的两个 402 码一一对应，文案不同，别合并。 */
export type SendPrecheck = { ok: true } | { ok: false; reason: "insufficient" | "outstanding" };

/**
 * 🔴 发送前预检（承重核心）—— 只拦「**账上必然发不出**」的两种情形，且要分清是哪一种。
 *
 * BE 口径（PR #239 `e2bc2c02` `_apply_reasoning_wallet_change` 的 reserve 分支，逐条对齐）：
 *   :557  `available < 0`                 → 402 `AIBRAIN_OUTSTANDING_BALANCE`（**欠费**，先补齐）
 *   :570  `available < requested`         → 402 `AIBRAIN_INSUFFICIENT_BALANCE`（预留不够，充值即可）
 * 两条的**次序**也照抄：负余额先判，否则欠费用户会被当成普通的「余额不足」，看到一句不相干的
 * 「这是临时预留、结束会退回」——他上一次的钱早就花掉了，退不回来。
 *
 * requested 是动态预留（提示词估算 × 1.25 + 完整 completion 配额），**前端算不出**（见
 * `minReservationCredits` 的注释）。故 `available === 0` 之外的正余额一律**故意放行**，由 BE 裁决：
 * 前端拿下界去拦会把「上下文短、实际只需几十积分」的用户误锁在门外（宁可多一次往返，不可错杀）。
 * ⚠️ `available` 为 `undefined`（钱包**未加载/加载失败**）同样不预拦（CR#2），理由同上。
 */
export function precheckSend(availableCredits: number | undefined): SendPrecheck {
  if (typeof availableCredits !== "number") return { ok: true };
  if (availableCredits < 0) return { ok: false, reason: "outstanding" };
  if (availableCredits === 0) return { ok: false, reason: "insufficient" };
  return { ok: true };
}

// ── 402 的结构化 detail（PR #239 `e2bc2c02`）─────────────────────────────────────────────────
// 🔴 契约变更史（别删，这段解释了为什么下面有两条并存的取数路径）：
//    · `42db0ecb` 时 `app_error_handler` **只转发 code + message**，required/available 仅存在于
//      那句英文自由文本里。当时前端**故意不解析它**（BE 改措辞就会静默失效，且没有测试会红），
//      改用可验证的下界 + 「至少」措辞。
//    · `e2bc2c02` 给 `AppError` 加了 `detail` 形参并在 handler 里转发（core/exceptions.py:24/94），
//      两类 402 各自带上了结构化字段。**现在能给精确值了**，措辞里的「至少」随之去掉。
//    · 下界那条路径**保留为回退**：BE 哪天不发 detail（回滚 / 新增第三种 402 忘了带），UI 退回
//      「至少 X」而不是什么都不显示。承重里专门有一条钉住这个回退。

/** `AIBRAIN_INSUFFICIENT_BALANCE` 的 detail（BE aibrain.py:578-583，逐字段）。 */
export interface InsufficientBalanceDetail {
  required_credits: number;
  available_credits: number;
  shortfall_credits: number;
  /** BE 恒发 `true`——「这是临时预留」这件事由 BE 自己标注，不是前端猜的。 */
  temporary_reservation: boolean;
}

/** `AIBRAIN_OUTSTANDING_BALANCE` 的 detail（BE aibrain.py:564-567，逐字段）。 */
export interface OutstandingBalanceDetail {
  /** 当前可用——**负数**（就是欠的那部分）。 */
  available_credits: number;
  /** 欠款额 = `-available_credits`，正数。 */
  outstanding_credits: number;
}

/** `detail` 过来的是 `unknown`（JSON 任意值）→ 逐字段校验后再用，形状不符一律当没有。 */
function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

export function parseInsufficientDetail(detail: unknown): InsufficientBalanceDetail | undefined {
  if (typeof detail !== "object" || detail === null) return undefined;
  const d = detail as Record<string, unknown>;
  const required = finiteNumber(d.required_credits);
  const available = finiteNumber(d.available_credits);
  const shortfall = finiteNumber(d.shortfall_credits);
  // required 是这条路径的最小充分信息；缺它就退回下界，**不拼半份数据**（拼出来的组合最误导）。
  if (required === undefined || available === undefined || shortfall === undefined) return undefined;
  return {
    required_credits: required,
    available_credits: available,
    shortfall_credits: shortfall,
    temporary_reservation: d.temporary_reservation === true
  };
}

export function parseOutstandingDetail(detail: unknown): OutstandingBalanceDetail | undefined {
  if (typeof detail !== "object" || detail === null) return undefined;
  const d = detail as Record<string, unknown>;
  const available = finiteNumber(d.available_credits);
  const outstanding = finiteNumber(d.outstanding_credits);
  if (available === undefined || outstanding === undefined) return undefined;
  return { available_credits: available, outstanding_credits: outstanding };
}

/**
 * 402「预留不足」要向用户交代的数（§三 第 1/2/3 条；第 4 条是文案 `insufficientReserveNote`）。
 * `exact` 决定文案说「需要 X」还是「至少需要 X」—— 这不是措辞洁癖：把下界说成精确值，
 * 等于告诉用户「充这么多就够了」，而实际还要加上提示词那一段，充完照样发不出去。
 */
export interface ShortfallView {
  /** 需要多少（`exact=true` 时是 BE 的精确值，否则是 `minReservationCredits` 的下界）。 */
  required: number;
  /** true = 来自 BE 的结构化 detail；false = 前端算的下界，文案须配「至少」。 */
  exact: boolean;
  /** 当前可用积分；BE 未给且钱包未加载时 undefined（**不填 0**，0 是一个会误导的谎）。 */
  available?: number;
  /**
   * 还差多少。精确态取 BE 的 `shortfall_credits`；回退态仅当余额已知**且低于下界**时给出
   * （`available >= 下界` 却仍被 402 说明缺口在提示词那一段，前端算不出 → undefined，
   * 让 UI 换一句话说，**不显示「还差 0 积分」这种荒谬值**）。
   */
  shortfall?: number;
}

export function shortfallView(
  tier: IntensityTier,
  availableCredits: number | undefined,
  detail?: unknown
): ShortfallView {
  const parsed = parseInsufficientDetail(detail);
  if (parsed) {
    return {
      required: parsed.required_credits,
      exact: true,
      available: parsed.available_credits,
      shortfall: parsed.shortfall_credits > 0 ? parsed.shortfall_credits : undefined
    };
  }
  // ── 回退：BE 没给 detail（旧版本 / 回滚 / 预检拦截根本没发请求）→ 下界 + 「至少」措辞 ──
  const required = minReservationCredits(tier);
  const available = typeof availableCredits === "number" ? availableCredits : undefined;
  const shortfall = available !== undefined && available < required ? required - available : undefined;
  return { required, exact: false, available, shortfall };
}

/**
 * 402「欠费」要交代的数。与 `ShortfallView` **刻意不合并**：两者的话术相反 ——
 * 一个是「这笔钱只是临时锁住、结束会退」，另一个是「上次那笔已经花掉了、要补上」。
 * @returns detail 与钱包都拿不到数时返回 undefined → UI 只给定性文案，不编数字。
 */
export interface OutstandingView {
  /** 欠款额（正数）。 */
  outstanding: number;
  /** 当前余额（负数）。 */
  available?: number;
}

export function outstandingView(detail: unknown, availableCredits?: number): OutstandingView | undefined {
  const parsed = parseOutstandingDetail(detail);
  if (parsed) return { outstanding: parsed.outstanding_credits, available: parsed.available_credits };
  // 回退：钱包的 available 现在**可以是负数**（BE 去掉了 `next_available < 0` 的断言），
  // 负余额本身就等于欠款额，是可靠的第二来源。
  if (typeof availableCredits === "number" && availableCredits < 0)
    return { outstanding: -availableCredits, available: availableCredits };
  return undefined;
}

/**
 * 402 之三：**在途敞口打满**（`AIBRAIN_INFLIGHT_EXPOSURE_LIMIT`，BE `2a98b5d0` aibrain.py:1405-1420）。
 *
 * 🔴 这一条与前两个 402 的根本区别：**它不是余额问题，充值不解决**。
 *    BE `_tenant_inflight_exposure_limit()` = `_max_single_request_exposure_credits() × multiplier`，
 *    两个都是 config 常量（默认 multiplier=2）——钱包余额**不在这个式子里**。用户充再多钱，
 *    上限一分不涨；唯一能做的是等在途请求答完（每条 pending 的 user 消息占一个 Sol 尺寸槽位）。
 *
 * 🔴 detail 里 BE 一共给了 6 个字段，前端**只用两个**：
 *    `in_flight_request_count` → 「当前有 N 条对话正在进行中」，用户能据此行动（等它们答完）
 *    `retryable`               → 决定说不说「稍后重试即可」
 *    另外四个（in_flight_exposure_credits / requested_exposure_credits / exposure_limit_credits /
 *    excess_credits）都是**积分口径的敞口额度**，看着像钱其实不是钱 —— 把「还差 3200 积分敞口」
 *    摆给用户，只会让他去充值，而充值恰恰无效。**故意不展示**，这不是遗漏。
 */
export interface InflightExposureView {
  /** 当前在途请求数；detail 缺失时 undefined → 文案不提数字。 */
  inFlightRequests?: number;
  /** 是否可重试。BE 目前恒发 `true`；detail 缺失时按 true 处理（重试最坏只是再看到同一条提示）。 */
  retryable: boolean;
}

/**
 * 503 用量异常冷却的 detail（FIX4 · BE `b91e2188` aibrain.py:1408）。
 *
 * 🔴 **这个字段是我上一轮那道门等来的**：FIX3 时 BE 不发 detail，我在 mock 契约门里断言
 *    `detail === undefined` 并写明「CA 一旦补上字段该门就红，那正是去接真值的时刻」。补上了，接。
 *    那道门本轮**改写而非删除** —— 职责从「标记未实现」变成「锁定已实现」（断言字段在且为正）。
 * 🔴 **回退保留**：字段缺失/非法时仍走「约一分钟」的硬编码默认值（config `default=60`），
 *    理由与 402 那次一模一样 —— BE 哪天不发，UI 会**静默**退化成什么都不说。回退也有门。
 * ⚠️ BE 仍**没有**发 `Retry-After` 头（全仓 grep 无），秒数只在这个 detail 里。
 */
export interface CooldownDetail {
  /** 还要等多少秒（BE `max(1, ceil(remaining))` → 正整数）。 */
  retry_after_seconds: number;
}

export interface CooldownView {
  /** BE 给的真值；缺失/非法时 undefined → 文案回落到「约一分钟」。 */
  retryAfterSeconds?: number;
}

export function cooldownView(detail: unknown): CooldownView {
  if (typeof detail !== "object" || detail === null) return {};
  const seconds = finiteNumber((detail as Record<string, unknown>).retry_after_seconds);
  // 非正数当拿不到：BE 保证 `>= 1`，出现 0/负数说明契约坏了，此时说「约一分钟」比说「请 0 秒后重试」强。
  return seconds !== undefined && seconds > 0 ? { retryAfterSeconds: Math.ceil(seconds) } : {};
}

export function inflightExposureView(detail: unknown): InflightExposureView {
  if (typeof detail !== "object" || detail === null) return { retryable: true };
  const d = detail as Record<string, unknown>;
  const count = finiteNumber(d.in_flight_request_count);
  return {
    // 0 条在途却报敞口满，说的话会自相矛盾（「当前有 0 条进行中」）→ 当作拿不到，只说定性。
    inFlightRequests: count !== undefined && count > 0 ? count : undefined,
    // 只有**显式** false 才当不可重试；缺失/非布尔一律按可重试（同上，较温和的一侧）。
    retryable: d.retryable !== false
  };
}
