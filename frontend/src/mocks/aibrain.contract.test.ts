import { beforeEach, describe, expect, it } from "vitest";

import { createConversation, getConversation, getWallet, sendMessage, topupWallet } from "@/lib/aibrain/api";
import { uploadImage } from "@/lib/api/uploads";
import type { SendMessageRequest } from "@/lib/aibrain/types";
import { resetAibrain } from "./aibrain-handlers";
import { registerMockAsset } from "./asset-registry";

// 🔴 mock 逐字段镜像 BE f2e9a2e0、**不比 BE 宽松**（含 402/422/502 状态码本身 + 充值幂等）。
beforeEach(() => resetAibrain());
const PROVIDER_FAIL = "__mock_provider_fail__"; // 与 mock 内约定一致
const OVERRUN = "__mock_reserve_overrun__"; // 触发「实扣超预留 → 追加预留」路径（同上，与 mock 内约定一致）
const EXPOSURE = "__mock_inflight_exposure__"; // FIX2：在途敞口打满 402
const USAGE_INVALID = "__mock_usage_limit__"; // FIX3：上游用量不可信 502（marker 名不变，语义已换）
const PROMPT_LIMIT = "__mock_prompt_limit__"; // FIX2：提示词超本地硬上限 422
const REPLAY_GUARD = "__mock_replay_guard__"; // FIX4：上游可能已产生成本 502（零扣费但开冷却）
// idempotency_key 是 BE 必填 UUID → 测试用真 UUID（每次唯一，避免跨用例串键）。
const topup = (amount: number, key = crypto.randomUUID()) => topupWallet({ amount, idempotency_key: key });

describe("aibrain mock 契约 · 对齐 BE 增量 1", () => {
  it("非法档位 → 422（校验错）", async () => {
    const conv = await createConversation();
    const body = { content: "hi", tier: "ultra", attachment_asset_ids: [] } as unknown as SendMessageRequest;
    await expect(sendMessage(conv.id, body)).rejects.toMatchObject({ status: 422 });
  });

  it("余额为 0（BE 新租户）→ 402 AIBRAIN_INSUFFICIENT_BALANCE", async () => {
    const conv = await createConversation();
    await expect(
      sendMessage(conv.id, { content: "hi", tier: "low", attachment_asset_ids: [] })
    ).rejects.toMatchObject({ status: 402, code: "AIBRAIN_INSUFFICIENT_BALANCE" });
  });

  // 🔴 PRICING-UI-0001 §四：**契约已变**——BE PR #239 删掉了 `_max_completion_tokens` 连同那条
  //    422 AIBRAIN_REQUEST_LIMIT_EXCEEDED（预留改为动态：答不下就追加预留，而不是先拒）。
  //    同一个场景（内容大到预留买不起）现在归 402 余额不足。本条从「断言 422」改为「断言 402」，
  //    是跟着 BE 契约走，不是为了让测试变绿而放宽。
  it("请求过大（动态预留买不起）→ 402 AIBRAIN_INSUFFICIENT_BALANCE（#239 起不再是 422）", async () => {
    await topup(100);
    const conv = await createConversation();
    await expect(
      sendMessage(conv.id, { content: "x".repeat(8001), tier: "high", attachment_asset_ids: [] })
    ).rejects.toMatchObject({ status: 402, code: "AIBRAIN_INSUFFICIENT_BALANCE" });
  });

  it("上游失败 → 502 AIBRAIN_PROVIDER_FAILED（不落通用报错）", async () => {
    await topup(100);
    const conv = await createConversation();
    await expect(
      sendMessage(conv.id, { content: PROVIDER_FAIL, tier: "low", attachment_asset_ids: [] })
    ).rejects.toMatchObject({ status: 502, code: "AIBRAIN_PROVIDER_FAILED" });
  });

  it("非法充值档位 → 422", async () => {
    await expect(topup(123)).rejects.toMatchObject({ status: 422 });
  });

  it("🔴 §四之二 充值幂等：同 key + 同额 重放 → 只加一次、返回首次结果（不双扣）", async () => {
    const key = crypto.randomUUID();
    const first = await topupWallet({ amount: 500, idempotency_key: key });
    expect(first.available_credits).toBe(500);
    const replay = await topupWallet({ amount: 500, idempotency_key: key }); // 网络重试
    expect(replay.available_credits).toBe(500); // 不是 1000
    const w = await getWallet();
    expect(w.available_credits).toBe(500);
    expect(w.total_topup_credits).toBe(500);
    // 不同 key = 新的一次充值 → 真的加。
    const next = await topup(500);
    expect(next.available_credits).toBe(1000);
  });

  it("🔴 FIX2 幂等键复用异额 → 409 AIBRAIN_IDEMPOTENCY_KEY_REUSED（同 key 同额才返首次）", async () => {
    const key = crypto.randomUUID();
    await topupWallet({ amount: 100, idempotency_key: key });
    await expect(topupWallet({ amount: 500, idempotency_key: key })).rejects.toMatchObject({
      status: 409,
      code: "AIBRAIN_IDEMPOTENCY_KEY_REUSED"
    });
    // 且没有第二笔：余额仍 100。
    expect((await getWallet()).available_credits).toBe(100);
  });

  it("🔴 FIX2 idempotency_key 缺失/非 UUID → 422（BE 必填 UUID，mock 不比 BE 宽松）", async () => {
    await expect(topupWallet({ amount: 100, idempotency_key: "not-a-uuid" })).rejects.toMatchObject({ status: 422 });
  });

  // 次序语义**原样保留**（预留闸在 provider 调用之前），只是被守的那个码随 #239 从 422 变成 402。
  it("🔴 次序：预留买不起 + 上游标记 → **402 先于 502**（预留不够时 provider 不被调用）", async () => {
    await topup(100);
    const conv = await createConversation();
    const content = PROVIDER_FAIL + "x".repeat(8001);
    await expect(sendMessage(conv.id, { content, tier: "high", attachment_asset_ids: [] })).rejects.toMatchObject({
      status: 402,
      code: "AIBRAIN_INSUFFICIENT_BALANCE"
    });
  });

  it("🔴 多传字段（BE extra=forbid）→ 422（mock 不比 BE 宽松，CR#4）", async () => {
    await topup(100);
    const conv = await createConversation();
    const body = { content: "hi", tier: "low", attachment_asset_ids: [], model: "sneaky" } as unknown as SendMessageRequest;
    await expect(sendMessage(conv.id, body)).rejects.toMatchObject({ status: 422 });
  });

  it("未知会话 → 404 AIBRAIN_CONVERSATION_NOT_FOUND", async () => {
    await expect(getConversation("no-such-conv")).rejects.toMatchObject({
      status: 404,
      code: "AIBRAIN_CONVERSATION_NOT_FOUND"
    });
  });

  // ══ PRICING-UI-0001 §四 · 计费三条路径承重 ═══════════════════════════════════════════
  // §七.3 要求「mock 与真实逻辑的一致性要有测试保护，否则下次改后端还会漂」。这三条钉的都是
  // **行为形态**（预留是不是动态的、差额退不退、追加预留失败会怎样），断言用**关系**而非具体数字
  // —— 断死 0.24192 这种值等于把测试焊在当前实现上，费率一动就得改测试，那种"红"没有信息量。

  /**
   * 🔴 路径① 正常预留 → 结算 → **差额立即退回**（§三 第 4 条要向用户保证的那件事）。
   * 变异A：把结算行 `available += max(0, reservation - charged)` 删掉（即预留全额扣走）
   *        → 「available 净减 = 实扣」这条红（净减会变成 27.5 而不是 0.24）。
   * 变异B：把预留改回 flat `min(200, available)` → 「预留 ≫ 实扣」仍绿，但**门②**会红（见下）。
   */
  it("路径①：预留 → 结算 → 差额立即退回（available 净减 = 实扣，而非预留额）", async () => {
    const w = await topup(500);
    expect(w.available_credits).toBe(500);
    const conv = await createConversation();
    const res = await sendMessage(conv.id, { content: "你好", tier: "low", attachment_asset_ids: [] });
    expect(res.assistant_message.status).toBe("completed");

    const reserved = res.user_message.reserved_credits ?? 0;
    const charged = res.assistant_message.charged_credits ?? 0;
    // 🔴 这就是本包 §三 存在的理由：被"扣住"的数远大于实际花掉的数。
    expect(charged).toBeGreaterThan(0);
    expect(reserved).toBeGreaterThan(charged * 10);
    // 🔴 差额退回：钱包净减少的**只有实扣**，那笔吓人的预留一分都没多占。
    expect(res.wallet.available_credits).toBeCloseTo(500 - charged, 6);
    expect(res.wallet.total_spent_credits).toBeCloseTo(charged, 6);
    // 同步 handler 里 reserve+settle 在一次请求内走完 → 读时 reserved 恒 0（BE 异步下不必然）。
    expect(res.wallet.reserved_credits).toBe(0);
  });

  /**
   * 🔴 门②：预留是**动态的**（随提示词长度涨），不是 flat 常量。
   * 变异：把 `reservationCredits` 换回 flat 200 / `min(200, available)` → 两次预留相等 → 本条红。
   * 这条与路径①职责分离：①管"差额退不退"，②管"预留是不是动态"，互不塌缩。
   */
  it("门②：预留随提示词长度动态变化（长内容的预留 > 短内容），不是 flat 常量", async () => {
    await topup(2000);
    const a = await createConversation();
    const b = await createConversation();
    const short = await sendMessage(a.id, { content: "嗨", tier: "low", attachment_asset_ids: [] });
    const long = await sendMessage(b.id, { content: "问题".repeat(500), tier: "low", attachment_asset_ids: [] });
    expect(long.user_message.reserved_credits ?? 0).toBeGreaterThan(short.user_message.reserved_credits ?? 0);
  });

  /**
   * 🔴🔴 PRICING-UI-0002：**双区间在计费里真的生效**。
   * 构造一个 prompt token 数跨过 272,000 阈值的请求，其**每 token 单价**必须高于低区间的请求。
   * 这是 mock 里唯一按"本次用量"计费的地方（`userCredits` → `rateForPromptTokens`），
   * 也就是双区间唯一能被观察到的地方；写死 `standard` 会让这条红。
   *
   * ⚠️ 怎么把 prompt 顶过 272K：靠**图片附件**（每张按 4096 token 计，BE `_IMAGE_PROMPT_TOKEN_ESTIMATE`）
   *    造不出 27 万；靠文本要 27 万字符，测试里生成一次约 0.5MB 字符串 —— 可行且比图片路径更直接。
   *    ⚠️ 但那样会先撞**提示词硬上限 422**（UTF-8 字节 > 922,000）——中文每字 3 字节，27.2 万字
   *    就是 81.6 万字节，仍在限内；用 ASCII 更安全（1 字节/字符）。故用 ASCII 'x'。
   *    ASCII 的 token 估算是「每 4 字符 1 token」→ 要 272K token 需 ~109 万字符，那会超字节上限。
   *    → 改用**非 ASCII**（每字 1 token、3 字节）：27.3 万字 = 27.3 万 token、81.9 万字节，两头都过。
   */
  it("🔴 PRICING-UI-0002：prompt 跨过 272K 阈值 → 单位 token 单价更高（双区间真的生效）", async () => {
    await topup(2000);
    const low = await createConversation();
    const high = await createConversation();

    const shortRes = await sendMessage(low.id, { content: "你好", tier: "low", attachment_asset_ids: [] });
    // 27.3 万个非 ASCII 字符 → ~27.3 万 prompt token（> 272,000），且 81.9 万字节（< 922,000）。
    const longRes = await sendMessage(high.id, {
      content: "国".repeat(273_000),
      tier: "low",
      attachment_asset_ids: []
    });

    const unit = (r: typeof shortRes) =>
      (r.assistant_message.charged_credits ?? 0) / (r.assistant_message.total_tokens || 1);
    // 🔴 高区间的每 token 单价必须更高 —— 低区间输入 1.12、高区间 2.24。
    expect(unit(longRes)).toBeGreaterThan(unit(shortRes));
    expect(longRes.assistant_message.prompt_tokens ?? 0).toBeGreaterThan(272_000);
  });

  /**
   * 🔴 门③：预留额**分档不同**（高档预留 ≫ 低档），即预留确实按费率算 —— §三 表格里 27.5/68.8/137.6 的来源。
   * 变异：`reservationCredits` 忽略 tier（写死用某一档费率）→ 本条红。
   */
  it("门③：同一句话在 high 档的预留显著高于 low 档（预留按档位费率算）", async () => {
    await topup(2000);
    const a = await createConversation();
    const b = await createConversation();
    const low = await sendMessage(a.id, { content: "你好", tier: "low", attachment_asset_ids: [] });
    const high = await sendMessage(b.id, { content: "你好", tier: "high", attachment_asset_ids: [] });
    expect(high.user_message.reserved_credits ?? 0).toBeGreaterThan((low.user_message.reserved_credits ?? 0) * 4);
  });

  /**
   * 🔴 路径② 余额不足 → 402，且闸门是 `available < 本次预留`（**不是旧 mock 的 available <= 0**）。
   * 变异：把闸门改回 `available <= 0` → 本条红（账上 10 分会被放行）。
   */
  it("路径②：账上有钱但不够本次预留 → 402（闸门是 available < 预留，不是 available<=0）", async () => {
    await topup(100); // 100 分，够 low（≈27.5）但远不够 high（≈137.6）
    const conv = await createConversation();
    await expect(
      sendMessage(conv.id, { content: "你好", tier: "high", attachment_asset_ids: [] })
    ).rejects.toMatchObject({ status: 402, code: "AIBRAIN_INSUFFICIENT_BALANCE" });
    // 被拒 = 一分没动（预留失败不该留下任何扣减）。
    expect((await getWallet()).available_credits).toBe(100);
  });

  /**
   * 🔴🔴 路径③ **透支交付**（FIX1 · 方向与上一版整个相反）。
   * BE `e2bc2c02`（aibrain.py:250-266）：provider 已经把答案生成出来了（上游的钱已经花了），
   * 此时才发现实扣超预留且追不上 → **不再拒绝**，而是照常交付 + 按实际用量结算 + **允许余额变负**。
   * 上一版 mock 是「追加失败 → 释放预留 → 402、不交付」，方向反了，会让前端测试建立在假象上。
   * 变异A：把结算改回 `available += max(0, reservation - charged)`（夹逼不许变负）→ 本条红（余额不为负）。
   * 变异B：恢复成「402 不交付」→ 本条红（拿不到 assistant_message）。
   */
  it("路径③：实扣超出预留 → **照常交付答案**，按实扣结算，余额允许变负（透支）", async () => {
    await topup(100);
    const conv = await createConversation();
    const res = await sendMessage(conv.id, { content: `${OVERRUN} 你好`, tier: "low", attachment_asset_ids: [] });
    // 🔴 答案**交付了**——上游的钱花了，不能既扣钱又不给答案，也不能白花钱不交付。
    expect(res.assistant_message.status).toBe("completed");
    expect(res.assistant_message.content.length).toBeGreaterThan(0);
    const charged = res.assistant_message.charged_credits ?? 0;
    const reserved = res.user_message.reserved_credits ?? 0;
    expect(charged).toBeGreaterThan(reserved); // 确实超预留了
    // 🔴 余额变负 = 欠费，且净减少恰好是实扣。
    expect(res.wallet.available_credits).toBeLessThan(0);
    expect(res.wallet.available_credits).toBeCloseTo(100 - charged, 6);
  });

  /**
   * 🔴 路径④ **负余额闸门**（FIX1 新增，BE reserve 分支 :557，判在预留不足之前）：
   * 透支之后，一切新的付费请求被 402 `AIBRAIN_OUTSTANDING_BALANCE` 拦住，直到补齐。
   * ⚠️ 上一版 mock **故意没有**这条，理由是「BE 无此逻辑，造一个是反方向的假杀」——那个判断在
   *    `42db0ecb` 上是对的；`e2bc2c02` 之后 BE 有了，所以现在必须有。
   * 变异：删掉 mock 的 `available < 0` 闸门 → 本条红（会放行并返回一个正常回答）。
   */
  it("路径④：透支后余额为负 → 新请求被 402 AIBRAIN_OUTSTANDING_BALANCE 拦住（不是 INSUFFICIENT）", async () => {
    await topup(100);
    const conv = await createConversation();
    await sendMessage(conv.id, { content: `${OVERRUN} 你好`, tier: "low", attachment_asset_ids: [] });
    expect((await getWallet()).available_credits).toBeLessThan(0);

    // 🔴 码必须是 OUTSTANDING 而不是 INSUFFICIENT —— 两者文案相反，串了就是对用户说错话。
    const err = await sendMessage(conv.id, { content: "再问一句", tier: "low", attachment_asset_ids: [] }).catch((e) => e);
    expect(err.status).toBe(402);
    expect(err.code).toBe("AIBRAIN_OUTSTANDING_BALANCE");
    // detail 逐字段对齐 BE aibrain.py:564-567。
    expect(err.detail.outstanding_credits).toBeGreaterThan(0);
    expect(err.detail.available_credits).toBeLessThan(0);
    expect(err.detail.outstanding_credits).toBeCloseTo(-err.detail.available_credits, 6);
  });

  /** 充值补齐欠款后即可继续（闸门是"余额转正"而非某个一次性标记）。 */
  it("路径④b：补齐欠款（充值）后 → 新请求恢复正常", async () => {
    await topup(100);
    const conv = await createConversation();
    await sendMessage(conv.id, { content: `${OVERRUN} 你好`, tier: "low", attachment_asset_ids: [] });
    await topup(500); // 补齐并有余
    expect((await getWallet()).available_credits).toBeGreaterThan(0);
    const res = await sendMessage(conv.id, { content: "再问一句", tier: "low", attachment_asset_ids: [] });
    expect(res.assistant_message.status).toBe("completed");
  });

  /**
   * 🔴 FIX1：402「预留不足」现在**带结构化 detail**（BE `e2bc2c02` 给 AppError 加了 detail 形参并在
   * `app_error_handler` 里转发）。上一版这条断言的是 `detail` 为 undefined —— 当时属实，且当时就写明
   * 「哪天 CA 补了字段本条会红，那正是去接精确值的时刻」。现在正是那一刻，改为钉住新形状。
   * 变异：mock 的 402 不带 detail → 本条红（前端读 detail 的路径就会静默退回下界而无人知）。
   */
  // ══ FIX2 · 三个新码的 mock 契约（detail 逐字段对齐 `2a98b5d0` 源码）═══════════════════════
  /**
   * 🔴 敞口 402 的 detail 有 **6 个字段**，逐字对齐 BE aibrain.py:1412-1419。
   * 其中 `in_flight_request_count` 的名字 CA 回执前后给过两个版本（`in_flight_requests` /
   * `in_flight_request_count`）——**以源码为准**，这条钉住的就是源码那个名字。
   * 变异：mock 把它写成 `in_flight_requests` → 本条红（前端也就拿不到条数）。
   */
  it("🔴 敞口 402 detail 六字段齐全，计数字段名是 in_flight_request_count（不是 in_flight_requests）", async () => {
    await topup(2000);
    const conv = await createConversation();
    const err = await sendMessage(conv.id, { content: `${EXPOSURE} 你好`, tier: "low", attachment_asset_ids: [] }).catch((e) => e);
    expect(err.status).toBe(402);
    expect(err.code).toBe("AIBRAIN_INFLIGHT_EXPOSURE_LIMIT");
    expect(Object.keys(err.detail).sort()).toEqual(
      [
        "excess_credits",
        "exposure_limit_credits",
        "in_flight_exposure_credits",
        "in_flight_request_count",
        "requested_exposure_credits",
        "retryable"
      ].sort()
    );
    expect(err.detail.in_flight_request_count).toBeGreaterThan(0);
    expect(err.detail.retryable).toBe(true);
    // 🔴 上限是 config 常量（单请求最大敞口 × multiplier），**与余额无关** —— 这一条 402 充值无解。
    expect(err.detail.exposure_limit_credits).toBeGreaterThan(err.detail.requested_exposure_credits);
  });

  /** 敞口被拒 = 请求没发出去 → 钱包一分未动（预留在敞口闸门之后才落）。 */
  it("敞口 402 → 余额一分未动", async () => {
    await topup(2000);
    const conv = await createConversation();
    await sendMessage(conv.id, { content: `${EXPOSURE} 你好`, tier: "low", attachment_asset_ids: [] }).catch(() => {});
    expect((await getWallet()).available_credits).toBe(2000);
  });

  /**
   * 422 提示词超限：BE 判在**建消息与动钱包之前**（aibrain.py:379）→ 钱包不动、会话里不留消息。
   * 变异：mock 把这道闸挪到预留之后 → 「余额一分未动」仍绿，但「会话里没留消息」会红。
   */
  it("🔴 422 提示词超限：detail = {prompt_token_upper_bound, max_prompt_tokens}，且钱包与会话都不动", async () => {
    await topup(2000);
    const conv = await createConversation();
    const err = await sendMessage(conv.id, { content: `${PROMPT_LIMIT} 你好`, tier: "low", attachment_asset_ids: [] }).catch((e) => e);
    expect(err.status).toBe(422);
    expect(err.code).toBe("AIBRAIN_PROMPT_LIMIT_EXCEEDED");
    expect(err.detail.max_prompt_tokens).toBe(922_000);
    expect(err.detail.prompt_token_upper_bound).toBeGreaterThan(err.detail.max_prompt_tokens);
    expect((await getWallet()).available_credits).toBe(2000);
    expect((await getConversation(conv.id)).messages).toHaveLength(0);
  });

  /**
   * 🔴🔴 FIX3 · 502 契约**三处都变了**（本条上一版断言的是「码 = ..._LIMIT_EXCEEDED、detail 七字段」）：
   *   ① 码换成 `AIBRAIN_PROVIDER_USAGE_INVALID`
   *   ② **detail 整个没了** —— 断言它缺席，否则 mock 会比 BE 富（发一份 BE 根本不发的载荷，
   *      前端若据此写逻辑，真接口下必然拿不到）
   *   ③ 触发后本租户进入冷却（见下一条）
   * 资金行为不变且已定稿：`_fail_chat_message` → release 全额预留 + `UsageRecord.credits=0` → 零扣费。
   */
  it("🔴 502 用量不可信：码=USAGE_INVALID、**无 detail**、预留全额释放（零扣费）", async () => {
    await topup(2000);
    const conv = await createConversation();
    const err = await sendMessage(conv.id, { content: `${USAGE_INVALID} 你好`, tier: "low", attachment_asset_ids: [] }).catch((e) => e);
    expect(err.status).toBe(502);
    expect(err.code).toBe("AIBRAIN_PROVIDER_USAGE_INVALID");
    // 🔴 FIX5 · P2：BE 这条 AppError 不带 detail，但响应里这个键**恒在、值为 null**
    //    （`_error_response` 的 `model_dump` 没有 `exclude_none`）。此前断言 `toBeUndefined`
    //    锁的是 mock 自己省略该键的旧形状 —— 比 BE 松。
    expect(err.detail).toBeNull();
    expect((await getWallet()).available_credits).toBe(2000); // 预留释放干净 = 零扣费
  });

  /**
   * 🔴 FIX3 · **因果链**：用量异常之后本租户进入冷却，下一次请求得 503
   * （BE `_raise_if_provider_usage_anomaly_cooldown`，判在最前）。
   * 变异：mock 触发 502 时不置冷却标志 → 本条红（第二次请求会正常返回）。
   */
  /**
   * 🔴🔴 FIX4 · **这道门是上一轮埋的，这次自己报警了**。
   * FIX3 时它断言 `detail === undefined`，并写明「CA 一旦补上字段该门就红，那正是去接真值的时刻」。
   * `b91e2188` 补上了 `retry_after_seconds` → 门**改写而不是删除**：
   * 职责从「**标记未实现**」变成「**锁定已实现**」——断言字段在场且为正整数。
   * 变异：mock 的 503 不发 detail → 本条红（前端会静默退回「约一分钟」而无人知）。
   */
  it("🔴 503 冷却：出过用量异常之后被拦下，且 detail 带正整数 retry_after_seconds", async () => {
    await topup(2000);
    const conv = await createConversation();
    await sendMessage(conv.id, { content: `${USAGE_INVALID} 你好`, tier: "low", attachment_asset_ids: [] }).catch(() => {});

    const err = await sendMessage(conv.id, { content: "再问一句", tier: "low", attachment_asset_ids: [] }).catch((e) => e);
    expect(err.status).toBe(503);
    expect(err.code).toBe("AIBRAIN_PROVIDER_USAGE_ANOMALY_COOLDOWN");
    expect(err.detail.retry_after_seconds).toBeGreaterThan(0);
    expect(Number.isInteger(err.detail.retry_after_seconds)).toBe(true); // BE `max(1, ceil(...))`
  });

  /**
   * 🔴 FIX4 第六个码：`REPLAY_GUARD` 零扣费但**开冷却** —— 与 `PROVIDER_FAILED` 的分水岭就在这里。
   * 变异：mock 触发 REPLAY_GUARD 时不置冷却标志 → 后半段红（第二次请求会正常返回）。
   */
  it("🔴 502 REPLAY_GUARD：无 detail + 零扣费 + **开冷却**（下一次请求 503）", async () => {
    await topup(2000);
    const conv = await createConversation();
    const err = await sendMessage(conv.id, { content: `${REPLAY_GUARD} 你好`, tier: "low", attachment_asset_ids: [] }).catch((e) => e);
    expect(err.status).toBe(502);
    expect(err.code).toBe("AIBRAIN_PROVIDER_REPLAY_GUARD");
    expect(err.detail).toBeNull(); // 同上：键恒在、值为 null（FIX5 · P2）
    expect((await getWallet()).available_credits).toBe(2000); // 零扣费

    const next = await sendMessage(conv.id, { content: "再问一句", tier: "low", attachment_asset_ids: [] }).catch((e) => e);
    expect(next.status).toBe(503); // 🔴 立刻重试必撞冷却
  });

  /**
   * 🔴 `PROVIDER_FAILED` 是三个 502 里**唯一不开冷却**的（不在 BE 的 `_USER_COOLDOWN_ERROR_CODES` 里）。
   * 这条与上一条**互为对照**：同是 502、同是零扣费，冷却与否是它们唯一的差别，也正是前端
   * 必须给两套文案的原因。变异：mock 让 PROVIDER_FAILED 也置冷却 → 本条红。
   */
  it("🔴 502 PROVIDER_FAILED **不开冷却**（与 REPLAY_GUARD 的唯一差别）", async () => {
    await topup(2000);
    const conv = await createConversation();
    await sendMessage(conv.id, { content: `${PROVIDER_FAIL} 你好`, tier: "low", attachment_asset_ids: [] }).catch(() => {});

    const next = await sendMessage(conv.id, { content: "再问一句", tier: "low", attachment_asset_ids: [] });
    expect(next.assistant_message.status).toBe("completed"); // 照常可发
  });

  /**
   * 🔴 冷却是**租户级**的（BE 查询按 tenant_id 过滤、不限会话）→ 换个会话照样被拦。
   * 变异：mock 把冷却标志挂到会话上 → 本条红。
   */
  it("🔴 冷却是租户级：换一个会话照样 503（不是按会话隔离的）", async () => {
    await topup(2000);
    const a = await createConversation();
    const b = await createConversation();
    await sendMessage(a.id, { content: `${USAGE_INVALID} 你好`, tier: "low", attachment_asset_ids: [] }).catch(() => {});

    const err = await sendMessage(b.id, { content: "换个会话问", tier: "low", attachment_asset_ids: [] }).catch((e) => e);
    expect(err.status).toBe(503);
    expect(err.code).toBe("AIBRAIN_PROVIDER_USAGE_ANOMALY_COOLDOWN");
  });

  /** 冷却判在**最前**（BE 里紧跟会话校验，连提示词闸都在它之后）→ 提示词超限时也应报 503。 */
  it("🔴 闸门次序：冷却期内即使提示词超限也报 503（冷却判在提示词闸之前）", async () => {
    await topup(2000);
    const conv = await createConversation();
    await sendMessage(conv.id, { content: `${USAGE_INVALID} 你好`, tier: "low", attachment_asset_ids: [] }).catch(() => {});

    const err = await sendMessage(conv.id, { content: `${PROMPT_LIMIT} x`, tier: "low", attachment_asset_ids: [] }).catch((e) => e);
    expect(err.status).toBe(503);
    expect(err.code).toBe("AIBRAIN_PROVIDER_USAGE_ANOMALY_COOLDOWN");
  });

  /** 闸门次序照抄 BE：提示词硬上限比任何钱包闸门都早 —— 余额为 0 也应报 422 而不是 402。 */
  it("🔴 闸门次序：余额为 0 + 提示词超限 → 报 422（提示词闸在钱包闸之前）", async () => {
    const conv = await createConversation(); // 余额 0
    const err = await sendMessage(conv.id, { content: `${PROMPT_LIMIT} x`, tier: "low", attachment_asset_ids: [] }).catch((e) => e);
    expect(err.status).toBe(422);
    expect(err.code).toBe("AIBRAIN_PROMPT_LIMIT_EXCEEDED");
  });

  it("🔴 402「预留不足」带结构化 detail：required/available/shortfall/temporary_reservation", async () => {
    const conv = await createConversation();
    const err = await sendMessage(conv.id, { content: "hi", tier: "low", attachment_asset_ids: [] }).catch((e) => e);
    expect(err.status).toBe(402);
    expect(err.code).toBe("AIBRAIN_INSUFFICIENT_BALANCE");
    expect(err.detail.required_credits).toBeGreaterThan(0);
    expect(err.detail.available_credits).toBe(0);
    expect(err.detail.shortfall_credits).toBeCloseTo(err.detail.required_credits, 6);
    // 🔴 BE 恒发 true —— 「这是临时预留」由 BE 自己标注，不是前端猜的。
    expect(err.detail.temporary_reservation).toBe(true);
    // message 仍是英文自由文本（BE 没改这句）；前端**不解析它**，只读 detail。
    expect(err.message).toMatch(/required .*available/);
  });

  // ── P1-2：附件必须真存在于资产注册表（不再凭空伪造）──────────────────────────
  it("🔴 P1-2 有效附件（真上传 → asset_id）→ 接受、响应带 download_url", async () => {
    await topup(100);
    const { asset_id } = await uploadImage(new File([new Uint8Array([1, 2, 3])], "p.png", { type: "image/png" }));
    const conv = await createConversation();
    const res = await sendMessage(conv.id, { content: "看图", tier: "low", attachment_asset_ids: [asset_id] });
    expect(res.user_message.attachments[0].asset_id).toBe(asset_id);
    expect(res.user_message.attachments[0].download_url).toBeTruthy(); // 真缩略图 URL
  });

  it("🔴 P1-2 不存在的 asset → 404 AIBRAIN_ATTACHMENT_NOT_FOUND，且**不扣费**", async () => {
    await topup(100);
    const conv = await createConversation();
    await expect(
      sendMessage(conv.id, { content: "看图", tier: "low", attachment_asset_ids: ["missing-asset"] })
    ).rejects.toMatchObject({ status: 404, code: "AIBRAIN_ATTACHMENT_NOT_FOUND" });
    expect((await getWallet()).available_credits).toBe(100); // 附件校验先于扣费 → 余额不动
  });

  it("🔴 P1-2 非图片 / 非 ready asset → 422 AIBRAIN_ATTACHMENT_INVALID", async () => {
    await topup(100);
    registerMockAsset({ asset_id: "doc-1", asset_type: "document", mime_type: "application/pdf", status: "ready", download_url: "x" });
    const conv = await createConversation();
    await expect(
      sendMessage(conv.id, { content: "看图", tier: "low", attachment_asset_ids: ["doc-1"] })
    ).rejects.toMatchObject({ status: 422, code: "AIBRAIN_ATTACHMENT_INVALID" });
  });

  it("🔴 P1-2 attachment_asset_ids 非数组 → 422（不再静默转空数组）", async () => {
    await topup(100);
    const conv = await createConversation();
    const body = { content: "hi", tier: "low", attachment_asset_ids: "nope" } as unknown as SendMessageRequest;
    await expect(sendMessage(conv.id, body)).rejects.toMatchObject({ status: 422 });
  });

  it("🔴 会话切换不串数据：两个会话各自独立的消息", async () => {
    await topup(500);
    const a = await createConversation();
    const b = await createConversation();
    await sendMessage(a.id, { content: "A的问题", tier: "low", attachment_asset_ids: [] });

    const detailA = await getConversation(a.id);
    const detailB = await getConversation(b.id);
    expect(detailA.messages.some((m) => m.content === "A的问题")).toBe(true);
    expect(detailB.messages).toHaveLength(0);
  });
});
