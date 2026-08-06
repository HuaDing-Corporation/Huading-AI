// 华鼎AI智脑 · MSW mock（AIBRAIN-UI-0001 · FIX1：逐字段镜像 BE f2e9a2e0，**不比 BE 宽松**，含 402/422/502 状态码本身）。
//
// 对齐点（以 BE 源码为准）：/wallet/topup（非 recharge）· 钱包 available_credits/... · 发消息体 attachment_asset_ids ·
// 响应含 wallet（非 balance）· 会话 404 AIBRAIN_CONVERSATION_NOT_FOUND。
// ⚠️ BE 增量 1 无 DELETE 会话、无文档上传（增量 3）→ 本 mock 不提供这两个端点（一期路径不调用）。
// ⚠️ 跨租户在 mock 不可表达（单租户 token）→ store 查不到 = 404，不伪造 tenant 自比（本项目既有诚实做法）。
//
// ── PRICING-UI-0001 §四 · 计费口径重写（对齐 PR #239 `codex/pricing-c3c4-be`）────────────────────
// 旧 mock 是 flat `min(200, available)` 预留 + `min(typical, reservation)` 结算，与真实逻辑已完全脱节
//（真实是**动态预留**：提示词估算 × 1.25 + 完整 completion 配额）。mock 比 BE 宽松 = 前端测试的绿是假绿。
// 现在逐条镜像 `backend/app/services/aibrain.py`（**契约基线 = #239 `b91e2188`**）：
//   预留 `_reservation_credits` = _user_credits(ceil(估算提示词 tokens × 1.25), max_completion_tokens=4096)
//   估算 `_estimate_prompt_tokens` / `_estimate_text_tokens`（ASCII 每 4 字符 1 token、非 ASCII 每字 1 token、
//        每条消息 +4、图片附件每张 4096）
//   闸门⁻¹ 用量异常冷却         → 503 AIBRAIN_PROVIDER_USAGE_ANOMALY_COOLDOWN（**判在最前**，
//          FIX4 起是**用户级**（新表 `AIBrainUserCooldown`）且带 `detail.retry_after_seconds`）
//   闸门⓪ 提示词超本地硬上限     → 422 AIBRAIN_PROMPT_LIMIT_EXCEEDED（建消息与动钱包之前）
//   闸门① `available < 0`      → 402 AIBRAIN_OUTSTANDING_BALANCE（**欠费**）
//   闸门② `available < requested` → 402 AIBRAIN_INSUFFICIENT_BALANCE（预留不足）
//   闸门③ 在途敞口打满           → 402 AIBRAIN_INFLIGHT_EXPOSURE_LIMIT（**与余额无关，充值无效**）
//   结算  settle 分支：`available += reserved - charged`（差额**立即退回**），reserved 归零
//   透支  实扣 > 预留时 BE **照常交付答案**并按实扣结算，余额**允许变负**（`allow_overdraft`）
//
// 🔴🔴 **本文件历代结论被 BE 推翻过四次，别照抄旧注释——每次都要回源码核**：
//   ✗ `42db0ecb` 时：「实扣超预留 → 释放预留 → 402、**不交付**」
//     → `e2bc2c02` 改成：provider 的钱已经花了 → **交付 + 透支**（方向整个相反）
//   ✗ `42db0ecb` 时：「不实现负余额闸门 —— BE 无此逻辑，造一个是反方向的假杀」
//     → `e2bc2c02` 改成：BE 有了（透支使余额可为负）→ **必须实现**
//   ✗ `2a98b5d0` 时：「`..._USAGE_LIMIT_EXCEEDED` 502 带 7 个 detail 字段」
//     → `fbe8420d` 改成：该码**删除**；「合法但超上限」转为 `min(reported, 上限)` **封顶扣费 + 正常交付**，
//       只剩「上报不可信」走 `..._USAGE_INVALID`（**无 detail**），且会让本租户进入 503 冷却
//   ✗ `fbe8420d` 时：「503 冷却**无 detail**，前端只能按 config 默认值说"约一分钟"」+「冷却是**租户级**」
//     → `b91e2188` 改成：补上 `detail.retry_after_seconds`（前端接真值、回退保留）；
//       冷却改用**用户级**独立表 `AIBrainUserCooldown`；并新增第六个码 `..._REPLAY_GUARD`
//       （零扣费但**开冷却**，与 `PROVIDER_FAILED` 的唯一差别就在这里）；
//       同期 `usage_charge_capped`/封顶扣费整个作废——改成 reported 全额扣、可负余额、成功后开冷却。
//   每一条在写下时都是对的；写它们的理由（不许 mock 与 BE 不一致，两个方向都不许）没变，变的是 BE。
//   留着这份对照，是让下一个人知道"该跟着谁改"，而不是把旧结论当教条。

import { http, HttpResponse } from "msw";

import {
  AIBRAIN_ERROR,
  MAX_COMPLETION_TOKENS,
  SINGLE_REQUEST_LIMIT,
  TIERS,
  TOPUP_OPTIONS,
  type ChatAttachment,
  type ChatMessage,
  type Conversation,
  type IntensityTier
} from "@/lib/aibrain/types";
import { getMockAsset, resetMockAssets } from "./asset-registry";

// 镜像 BE `_IMAGE_ASSET_TYPES` / `_IMAGE_MIME_TYPES`（services/aibrain.py:53-54）。
const IMAGE_ASSET_TYPES = new Set(["avatar_image", "product_image", "generated_image", "cover"]);
const IMAGE_MIME_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

const BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const ok = <T>(data: T, status = 200) =>
  HttpResponse.json({ data, error: null, request_id: "mock-req" }, { status });
/**
 * BE `_error_response`（core/exceptions.py）的形态：`error.detail` 是**可选**的结构化载荷。
 * 🔴 FIX1：`e2bc2c02` 给 `AppError` 加了 `detail` 形参并在 `app_error_handler` 里转发（:24/:94）——
 *    此前 detail 恒为 null。两类 402 的数字现在都在 detail 里，mock 必须照发，否则前端读 detail 的
 *    那条路径在 mock 下永远走不到（等于没测）。BE 不发 detail 时该字段缺席（不是 null 键），此处同形。
 */
const err = (status: number, code: string, message: string, detail?: unknown) =>
  HttpResponse.json(
    {
      data: null,
      error: { code, message, request_id: "mock-req", ...(detail === undefined ? {} : { detail }) },
      request_id: "mock-req"
    },
    { status }
  );

/** content 含此串 → 模拟上游失败（测 502 分流）。 */
const PROVIDER_FAIL_MARKER = "__mock_provider_fail__";
/**
 * content 含此串 → 模拟「答完才发现实扣超出预留」→ **透支交付**
 * （BE `e2bc2c02` aibrain.py:250-266 + `allow_overdraft`）。这条路径的特别之处：答案**照常交付**
 * （上游的钱已经花了，不能白花），差额记成欠款让余额变负，随后由闸门① 拦住一切新的付费请求。
 * ⚠️ 这与本 mock 上一版的方向**完全相反**（旧版是「不交付 + 释放预留」），别按旧印象改。
 */
const RESERVE_OVERRUN_MARKER = "__mock_reserve_overrun__";
/**
 * content 含此串 → 模拟**在途敞口打满** 402 `AIBRAIN_INFLIGHT_EXPOSURE_LIMIT`
 * （BE `2a98b5d0` aibrain.py:1405-1420）。
 * ⚠️ 为什么用 marker 而不是真的数并发：BE 的判据是「本租户 `status=pending` 的 user 消息数」，
 *    而本 mock 是**同步 handler** —— 请求进来就走完，永远不存在 pending 中间态，真实条件在这里
 *    根本无法自然发生。marker 换来的是这条分支在前端侧可测；行为形态（402 + 6 个 detail 字段）是真的。
 */
const INFLIGHT_EXPOSURE_MARKER = "__mock_inflight_exposure__";
/**
 * content 含此串 → 模拟**上游用量不可信** 502 `AIBRAIN_PROVIDER_USAGE_INVALID`
 * （BE `fbe8420d` aibrain.py:519-541，fail-closed 不交付、**无 detail**）。
 * ⚠️ FIX2 的 `..._USAGE_LIMIT_EXCEEDED` 连同它 7 个 detail 字段已被 BE 删除——「合法但超上限」
 *    改成封顶扣费 + 正常交付，不再是错误路径。marker 名保留不变，语义已换。
 * 🔴 触发它会让本租户进入**用量异常冷却**（见下），下一次请求得 503 —— 这条因果链是 BE 的真实行为，
 *    mock 必须照做，否则前端测「冷却」时得自己伪造状态，那就测不到"异常之后才冷却"这件事。
 */
const PROVIDER_USAGE_INVALID_MARKER = "__mock_usage_limit__";
/** content 含此串 → 强制触发提示词硬上限 422（免得测试要造 922KB 输入；真实字节数超限同样会触发）。 */
const PROMPT_LIMIT_MARKER = "__mock_prompt_limit__";
/**
 * content 含此串 → 模拟 **502 `AIBRAIN_PROVIDER_REPLAY_GUARD`**（FIX4 第六个码，`b91e2188`）：
 * 上游可能已经产生成本却没给出可用结果 → 零扣费但**开用户冷却**。**无 detail**。
 */
const REPLAY_GUARD_MARKER = "__mock_replay_guard__";

// ── 提示词本地硬上限（BE `_prompt_token_upper_bound` vs `engine_aibrain_max_prompt_tokens`）──
/** BE config 默认值（`engine_aibrain_max_prompt_tokens`，config.py:227，上界也是 922_000）。 */
const MAX_PROMPT_TOKENS = 922_000;
/**
 * BE `_prompt_token_upper_bound`：**按 UTF-8 字节数**取保守上界（GPT 系分词器产出的 token 数
 * 不会超过输入字节数），图片按固定配额，每条消息再加序列化开销。
 * 🔴 与 `estimatePromptTokens`（计费用的**估算**）是**两个不同的量**，别混：
 *    这个是"绝不会更多"的上界，用于安全闸；那个是"大概多少"的估算，用于预留额。
 *    BE 也是两个独立函数，mock 照搬这个区分。
 */
function promptTokenUpperBound(history: ChatMessage[], content: string, attachmentCount: number): number {
  const bytes = (s: string) => new TextEncoder().encode(s).length;
  let tokens = 0;
  for (const m of history) {
    if (m.content) tokens += bytes(m.content);
    tokens += m.attachments.length * IMAGE_PROMPT_TOKEN_ESTIMATE;
    tokens += 4;
  }
  if (content) tokens += bytes(content);
  tokens += attachmentCount * IMAGE_PROMPT_TOKEN_ESTIMATE;
  tokens += 4;
  return tokens;
}

// ── 计费镜像（逐条对应 BE services/aibrain.py，见文件抬头）──────────────────────────────────
const PROMPT_RESERVATION_MULTIPLIER = 1.25; // BE `_PROMPT_RESERVATION_MULTIPLIER`
const IMAGE_PROMPT_TOKEN_ESTIMATE = 4096; // BE `_IMAGE_PROMPT_TOKEN_ESTIMATE`
const CREDIT_QUANTUM = 1_000_000; // BE `_REASONING_CREDIT_QUANTUM = Decimal("0.000001")`

/** BE `_reasoning_credits`：量化到 6 位小数（浮点误差不许渗进钱包数字）。 */
const quantizeCredits = (value: number) => Math.round(value * CREDIT_QUANTUM) / CREDIT_QUANTUM;

/** BE `_user_credits`：prompt × 输入费率 + completion × 输出费率，再量化。 */
function userCredits(tier: IntensityTier, promptTokens: number, completionTokens: number): number {
  const { inputPer1k, outputPer1k } = TIERS[tier].rate;
  return quantizeCredits((promptTokens * inputPer1k + completionTokens * outputPer1k) / 1000);
}

/** BE `_estimate_text_tokens`：ASCII 每 4 字符 1 token（向上取整），非 ASCII 每字 1 token，至少 1。 */
function estimateTextTokens(text: string): number {
  const chars = Array.from(text); // 按 code point 数，对齐 Python 的 len()
  const ascii = chars.filter((ch) => (ch.codePointAt(0) ?? 0) < 128).length;
  return Math.max(1, Math.floor((ascii + 3) / 4) + (chars.length - ascii));
}

/**
 * BE `_estimate_prompt_tokens`：逐条消息累加（文本按上式、每张图片 4096、每条消息再 +4），至少 1。
 * ⚠️ **未逐字复刻**的两点（诚实标注，别把 mock 当成 BE 的等价物）：
 *   ① BE 的上下文取最近 20 轮（40 条）；mock 直接全量算 —— mock 会话不会长到 40 条，两者等价。
 *   ② BE 把历史消息的图片附件也重建成 image_url part；此处同样按 4096/张 计入，但 `_provider_context`
 *      对历史附件的取舍细节我没有逐行核到，若 CA 那边不是这样，差的是**预留的绝对值**，不是行为形态。
 */
function estimatePromptTokens(history: ChatMessage[], content: string, attachmentCount: number): number {
  let tokens = 0;
  for (const m of history) {
    if (m.content) tokens += estimateTextTokens(m.content);
    tokens += m.attachments.length * IMAGE_PROMPT_TOKEN_ESTIMATE;
    tokens += 4;
  }
  if (content) tokens += estimateTextTokens(content);
  tokens += attachmentCount * IMAGE_PROMPT_TOKEN_ESTIMATE;
  tokens += 4;
  return Math.max(1, tokens);
}

/**
 * BE `_reservation_credits`：**动态预留** = 提示词估算 × 1.25（向上取整到整 token）+ 完整 completion 配额。
 * 🔴 这就是 §三 那个「用户看到一个远大于实际花费的数字被扣住」的来源：光 completion 段
 *    （4096 token）在 high 档就是 137.6 积分，而一次典型对话实扣不到 20。
 */
function reservationCredits(tier: IntensityTier, promptTokens: number): number {
  return userCredits(tier, Math.ceil(promptTokens * PROMPT_RESERVATION_MULTIPLIER), MAX_COMPLETION_TOKENS);
}

interface MockConversation extends Conversation {
  messages: ChatMessage[];
}

const conversations = new Map<string, MockConversation>();
let convSeq = 0;
let msgSeq = 0;
/**
 * 🔴 **用量异常冷却**（BE `_raise_if_provider_usage_anomaly_cooldown`）。
 * FIX3 引入、**FIX4 契约变了两处**：
 *   ① 判据从「窗口内有 failed 消息」换成**独立的冷却表** `AIBrainUserCooldown`（`b91e2188`）
 *   ② 粒度从**租户级**变成**用户级**（该表按 `user_id` 唯一索引，查询带 tenant_id + user_id）
 *   ③ 触发码多了一个 `AIBRAIN_PROVIDER_REPLAY_GUARD`（`_USER_COOLDOWN_ERROR_CODES` 三个码）
 *   ④ 503 现在**带 detail**：`{retry_after_seconds}`（`max(1, ceil(剩余秒))`）
 * ⚠️ mock 用**标志 + 固定秒数**而不是真时间窗：同步 handler 里没有真实时钟推进，用真时间窗会让
 *    测试要么等 60 秒、要么注入假时钟 —— 两者都比这个更脆。被守的行为（**出过用量异常之后，
 *    下一次请求被 503 拦下、且带得出剩余秒数**）是真的；"窗口过期后自动解除"这一段 mock **不表达**。
 * ⚠️ mock 是单用户环境（单租户 token），**用户级与租户级在此无法区分** —— 换会话仍被拦这一点
 *    两种粒度下都成立，故既有的"跨会话"门在 FIX4 之后依然有效，只是它证明不了粒度本身。诚实标注。
 */
let usageAnomalyCooldown = false;
/** mock 的冷却剩余秒数 = BE config `default=60`（真实实现里是按 expires_at 算的剩余量）。 */
const COOLDOWN_RETRY_AFTER_SECONDS = 60;
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
  usageAnomalyCooldown = false;
  resetMockAssets();
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

/**
 * 从**注册表**取已校验的资产构造响应附件（FIX4：不再凭空伪造）。
 * @returns 全部 asset_id 合法 → 附件数组；否则第一个不合法的错误（404 未注册 / 422 非图片或非 ready）。
 */
function resolveAttachments(ids: string[]): { attachments: ChatAttachment[] } | { error: ReturnType<typeof err> } {
  const attachments: ChatAttachment[] = [];
  for (const asset_id of ids) {
    const asset = getMockAsset(asset_id);
    // 查不到 = 不存在/跨租户/已删 → 404（BE aibrain.py:633）。
    if (!asset) return { error: err(404, AIBRAIN_ERROR.ATTACHMENT_NOT_FOUND, "附件不存在或无权访问") };
    // 非 ready / 非图片类型 / 非图片 MIME → 422（BE aibrain.py:641-647）。
    if (asset.status !== "ready" || !IMAGE_ASSET_TYPES.has(asset.asset_type) || !IMAGE_MIME_TYPES.has(asset.mime_type))
      return { error: err(422, AIBRAIN_ERROR.ATTACHMENT_INVALID, "附件类型不支持（仅 ready 的 JPG/PNG/WebP 图片）") };
    attachments.push({ asset_id, asset_type: asset.asset_type, mime_type: asset.mime_type, download_url: asset.download_url });
  }
  return { attachments };
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

    // ── 删除 / 清空（HISTORY-CHAT-DELETE-UI-0001，契约 §5.2；mock 先行——BE aibrain 路由本无 DELETE）──
    // 🔴 mock 纪律：**真的从会话表里去掉**（不是返 200 而列表照旧）——否则「删除后列表刷新」永远测不出来。
    // 语义镜像 BE 软删：这里直接从内存 Map 移除 = 用户侧「列表已过滤掉」的等效可观察结果（mock 不建 deleted_at
    // 影子表，因为**没有任何前端路径能观察到软删记录**；如此不会比 BE 宽松）。⚠️ 集合级 DELETE 必须注册在
    // `/:id` **之前**，否则会被 `:id` 影子覆盖（msw 按注册序匹配，":id" 会吃掉集合路径）。
    // ⚠️ 不碰 wallet/账本：available/totalTopup 一分不动（验收 4）。
    http.delete(`${BASE}/api/v1/aibrain/conversations`, () => {
      const deleted_count = conversations.size;
      conversations.clear();
      return ok({ deleted_count });
    }),
    http.delete(`${BASE}/api/v1/aibrain/conversations/:id`, ({ params }) => {
      const id = String(params.id);
      // 跨租户/不存在 → 404（与详情同码，BE 租户过滤后即"不存在"）。
      if (!conversations.has(id)) return err(404, AIBRAIN_ERROR.CONVERSATION_NOT_FOUND, "会话不存在或无权访问");
      // 失败注入口（承重门 5：删除失败 → 友好错误 + 列表不乐观移除）：id 含 __FAIL__ → 500，且**不移除**。
      if (id.includes("__FAIL__")) return err(500, "INTERNAL_ERROR", "删除失败");
      conversations.delete(id);
      return ok({ deleted: true });
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
      // 🔴 FIX4 · P1-2：**不再静默把非数组转空数组**——非法形状应报错（BE pydantic list[str] → 422）。
      if (body.attachment_asset_ids !== undefined && !Array.isArray(body.attachment_asset_ids))
        return err(422, "VALIDATION_ERROR", "attachment_asset_ids 必须是数组");
      const ids = Array.isArray(body.attachment_asset_ids) ? (body.attachment_asset_ids as string[]) : [];
      // 形状校验（BE：max 10、唯一、非空）。
      if (ids.length > 10 || ids.some((x) => typeof x !== "string" || !x.trim()) || new Set(ids).size !== ids.length)
        return err(422, "VALIDATION_ERROR", "attachment_asset_ids 非法（≤10、唯一、非空）");
      if (!content && ids.length === 0) return err(422, "VALIDATION_ERROR", "消息不能为空");
      // 🔴 FIX4 · P1-2：附件必须是**注册表里真存在**的 ready 图片资产（不再凭空伪造）→ 404/422（BE aibrain.py:612-647）。
      const resolved = resolveAttachments(ids);
      if ("error" in resolved) return resolved.error;

      const attachments = resolved.attachments;
      const tier = body.tier;

      // ── 闸门⁻¹ 用量异常冷却 → 503（BE `_raise_if_provider_usage_anomaly_cooldown`，:365）────────
      // 🔴 判在**最前**（BE 里紧跟 conversation_or_404，连提示词闸都在它之后）。次序照抄：
      //    冷却期内一切请求直接回绝，不做任何校验、不碰钱包。**无 detail、无 Retry-After 头**
      //    （BE 那条 AppError 只有 message/code/status；全仓无 retry-after）。
      if (usageAnomalyCooldown)
        return err(
          503,
          AIBRAIN_ERROR.PROVIDER_USAGE_ANOMALY_COOLDOWN,
          "AIBRAIN provider billing usage is temporarily unavailable. Try again later.",
          // 🔴 FIX4：BE `b91e2188` 补上了这个字段（此前无 detail）。mock 必须照发，
          //    否则前端读真值那条路径在 mock 下永远走不到，只会一直命中"约一分钟"的回退。
          { retry_after_seconds: COOLDOWN_RETRY_AFTER_SECONDS }
        );

      // ── 闸门⓪ 提示词超本地硬上限 → 422（BE aibrain.py:379，在**建消息与动钱包之前**）─────────
      // 次序照抄 BE：这一条比任何钱包闸门都早 —— 输入太长时消息压根不落库、钱包一分不动。
      // 真实条件（UTF-8 字节数 > 922000）在 mock 里也**真的**会触发；marker 只是让测试不必造 922KB 输入。
      {
        const upperBound = promptTokenUpperBound(conv.messages, content, ids.length);
        if (upperBound > MAX_PROMPT_TOKENS)
          return err(
            422,
            AIBRAIN_ERROR.PROMPT_LIMIT_EXCEEDED,
            "AIBRAIN prompt exceeds the local safety limit.",
            { prompt_token_upper_bound: upperBound, max_prompt_tokens: MAX_PROMPT_TOKENS }
          );
        if (content.includes(PROMPT_LIMIT_MARKER))
          return err(
            422,
            AIBRAIN_ERROR.PROMPT_LIMIT_EXCEEDED,
            "AIBRAIN prompt exceeds the local safety limit.",
            { prompt_token_upper_bound: MAX_PROMPT_TOKENS + 1, max_prompt_tokens: MAX_PROMPT_TOKENS }
          );
      }

      // ── 闸门① 负余额（欠费）→ 402 OUTSTANDING（BE reserve 分支 :557，**判在最前**）───────────
      // 🔴 FIX1：上一版 mock 没有这条，理由是「BE 无此逻辑，造一个是反方向的假杀」——`e2bc2c02` 之后
      //    BE 有了：结算允许透支（见下方路径③），余额可为负，随后一切新的付费请求被这道闸门拦住。
      if (available < 0)
        return err(
          402,
          AIBRAIN_ERROR.OUTSTANDING_BALANCE,
          "Pay the outstanding AIBRAIN balance before starting new work.",
          { available_credits: available, outstanding_credits: quantizeCredits(-available) }
        );

      // ── 闸门② 预留不足 → 402 INSUFFICIENT（BE reserve 分支 :570）─────────────────────────────
      // 🔴 闸门是 `available < requested`（**不是旧 mock 的 available<=0**）：账上有钱但不够这次预留，
      //    照样 402 —— 这正是 §三 要向用户解释清楚的那个 402。
      const promptTokens = estimatePromptTokens(conv.messages, content, attachments.length);
      const reservation = reservationCredits(tier, promptTokens);
      if (available < reservation)
        return err(
          402,
          AIBRAIN_ERROR.INSUFFICIENT_BALANCE,
          // message 仍是英文自由文本（BE 没改这句）；**数字现在也在 detail 里**，前端读 detail。
          `Insufficient reasoning balance for this request (required ${reservation}, available ${available}).`,
          {
            required_credits: reservation,
            available_credits: available,
            shortfall_credits: quantizeCredits(reservation - available),
            temporary_reservation: true
          }
        );
      // ── 闸门③ 在途敞口打满 → 402 INFLIGHT_EXPOSURE（BE reserve 分支 :927，**判在预留不足之后**）──
      // 🔴 它也是 402，但**跟余额毫无关系**：上限 = 单请求最大敞口 × multiplier，两个都是 config 常量。
      //    前端因此绝不许对这个码引导充值 —— mock 照发 6 个 detail 字段，好让那条分流真的被测到。
      if (content.includes(INFLIGHT_EXPOSURE_MARKER))
        return err(
          402,
          AIBRAIN_ERROR.INFLIGHT_EXPOSURE_LIMIT,
          "Too much AIBRAIN work is already in progress. Wait for an existing request to finish before retrying.",
          {
            in_flight_exposure_credits: 5300.8256,
            requested_exposure_credits: 5300.8256,
            exposure_limit_credits: 10601.6512,
            excess_credits: 0.0001,
            in_flight_request_count: 2,
            retryable: true
          }
        );

      available = quantizeCredits(available - reservation); // 预留：available → reserved

      // 上游失败（测 502 分流）——发生在预留之后，故要**把预留还回去**（BE `_fail_chat_message` 释放预留）。
      // ⚠️ `PROVIDER_FAILED` 是三个 502 里**唯一不开冷却**的（不在 `_USER_COOLDOWN_ERROR_CODES` 里）
      //    —— 这正是它与 REPLAY_GUARD 的分水岭，故此处**不**置冷却标志。
      if (content.includes(PROVIDER_FAIL_MARKER)) {
        available = quantizeCredits(available + reservation);
        return err(502, AIBRAIN_ERROR.PROVIDER_FAILED, "AI 服务暂时不可用，请稍后重试");
      }

      // 🔴 FIX4 第六个码：上游**可能已经产生成本**却没给出可用结果（BE aibrain.py:486-512/:628-632）。
      //    零扣费（同样 release 全额预留），但**会开用户冷却** —— 这是它与 PROVIDER_FAILED 的唯一
      //    但关键的区别：用户立刻重试必撞 503。detail 为空（BE 只给 code+message）。
      if (content.includes(REPLAY_GUARD_MARKER)) {
        available = quantizeCredits(available + reservation);
        usageAnomalyCooldown = true;
        return err(
          502,
          AIBRAIN_ERROR.PROVIDER_REPLAY_GUARD,
          "AIBRAIN provider request may have incurred cost."
        );
      }

      // 上游用量不可信 → 502 fail-closed（BE `fbe8420d` aibrain.py:519-541）。在预留之后，故释放预留。
      // 🔴 FIX3 三处变化，逐条对齐：
      //   ① 码 `..._USAGE_LIMIT_EXCEEDED` → **`..._USAGE_INVALID`**
      //   ② **detail 整个去掉**（原来那 7 个字段在 BE 里已不存在——别再发，发了就是 mock 比 BE 富）
      //   ③ 触发后**本租户进入用量异常冷却**，下一次请求得 503（BE 靠"窗口内有 failed 且 error_code
      //      ∈ {USAGE_MISSING, USAGE_INVALID} 的消息"判定，mock 用标志位表达，见其定义处的说明）
      // 资金：`_fail_chat_message` → release 全额预留 + `UsageRecord.credits=0` → **零扣费**，
      //      这已是源码事实（FIX3 §一 定稿），前端文案据此写「未扣费」。
      if (content.includes(PROVIDER_USAGE_INVALID_MARKER)) {
        available = quantizeCredits(available + reservation);
        usageAnomalyCooldown = true;
        return err(
          502,
          AIBRAIN_ERROR.PROVIDER_USAGE_INVALID,
          "AIBRAIN provider returned invalid billing usage."
        );
      }

      const now = new Date(2026, 6, 19, 10, 30, msgSeq).toISOString();
      const answer = content.includes(RESERVE_OVERRUN_MARKER)
        ? "（mock）本次模拟「实扣超出预留」路径。"
        : `（${TIERS[tier].label}档 · ${TIERS[tier].model}）已收到：「${content || "[图片]"}」。这是 mock 回答，联调后由真实模型作答。`;
      // 实际用量：prompt 用预留时的同一估算，completion 用回答本身的长度 —— 于是响应里的
      // prompt_tokens/completion_tokens/charged_credits 三者**能互相验算**（不是拍脑袋的常数）。
      const completionTokens = estimateTextTokens(answer);
      let charged = userCredits(tier, promptTokens, completionTokens);

      // ── 路径③ 实扣超预留 → **透支交付**（BE `e2bc2c02` `_expand_reasoning_reservation` + :250-266）──
      // 🔴🔴 FIX1 **方向整个反过来了**。上一版 mock 是「追加失败 → 释放预留 → 402、不交付」；
      //     `e2bc2c02` 之后 BE 的行为是：
      //       provider 已经把答案生成出来了（钱已经花在上游了）→ 追加预留失败**不再拒绝**，
      //       而是 `overdraft_authorized = True` → **照常交付答案** → settle 时 `allow_overdraft=True`
      //       → 钱包**允许变负**（BE 同时删掉了 `next_available < 0` 的 RuntimeError 断言）
      //       → 随后由上面的闸门① 拦住一切新的付费请求，直到用户补齐。
      //     旧 mock 那个方向会让前端测试建立在「超支就不交付」的假象上，正是 CB 指出的第 3 点。
      // ⚠️ 倍数 5 是**测试钩子的放大量**，不是对 BE 的建模：真实世界里 `charged > reservation` 只可能
      //    因为提示词估算低估了（completion 那头被 `max_completion_tokens` 硬顶住，超不了）。而 prompt
      //    费率只有输出的 1/6，要靠堆 token 把实扣顶过预留，得几万个 token —— 在 mock 里造那种输入
      //    只会让用例难读。取 ×5 是为了**稳定命中这条分支**，行为形态（交付 + 透支）是真的。
      if (content.includes(RESERVE_OVERRUN_MARKER)) charged = quantizeCredits(reservation * 5);

      // ── 结算（BE settle 分支：`available += reserved - charged`）──────────────────────────
      // 🔴 差额**立即退回**：available 净减少的只有 charged，那个吓人的预留额一秒都不多占。
      //    这正是 §三 第 4 条要向用户讲清的事实，mock 必须真的这么行为，否则测试没法钉住它。
      // 🔴 `charged > reservation` 时这个式子自然得到**负余额** —— 不加 `Math.max(0, …)` 夹逼，
      //    因为 BE 就是允许它变负的（那正是欠费的来源）。
      available = quantizeCredits(available + reservation - charged);
      totalSpent = quantizeCredits(totalSpent + charged);

      const userMsg: ChatMessage = {
        id: `msg-${++msgSeq}`,
        conversation_id: conv.id,
        role: "user",
        content,
        attachments,
        tier,
        status: "completed",
        // 预留额落在 user_message 上（BE `user_message.reserved_credits = reservation`）——UI 若要展示
        // 「本次锁了多少」，取的是这个字段，不是 wallet 的 single_request_limit。
        reserved_credits: reservation,
        created_at: now
      };
      const assistantMsg: ChatMessage = {
        id: `msg-${++msgSeq}`,
        conversation_id: conv.id,
        role: "assistant",
        content: answer,
        attachments: [],
        tier,
        model: TIERS[tier].model,
        status: "completed",
        prompt_tokens: promptTokens,
        completion_tokens: completionTokens,
        total_tokens: promptTokens + completionTokens,
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
