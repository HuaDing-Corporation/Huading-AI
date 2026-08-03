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
   * 🔴 路径③ 追加预留失败（BE #239 `_expand_reasoning_reservation`）：**请求已经跑完**才被 402，
   * 预留全额释放 —— 用户花了时间、没拿到答案，但也**没被扣一分钱**。
   * 变异：删掉追加预留失败分支里的 `available += reservation`（不释放预留）→ 本条红（余额少了一截）。
   */
  it("路径③：实扣超出预留、追加预留又不够 → 402，且预留全额释放（余额一分未动）", async () => {
    await topup(100);
    const conv = await createConversation();
    await expect(
      sendMessage(conv.id, { content: `${OVERRUN} 你好`, tier: "low", attachment_asset_ids: [] })
    ).rejects.toMatchObject({ status: 402, code: "AIBRAIN_INSUFFICIENT_BALANCE" });
    expect((await getWallet()).available_credits).toBe(100);
  });

  /**
   * 🔴 §三 的实现前提：402 响应体里**没有**结构化的 required/available（BE `app_error_handler`
   * 只转发 code + message，detail 恒 null）。前端因此不去解析那句英文（见 lib/aibrain/types.ts
   * `ShortfallView` 注释），改用可验证的下界。这条把「BE 没给字段」这个事实钉住 ——
   * 哪天 CA 补了结构化字段，本条会红，那正是提醒去接精确值的时刻。
   */
  it("🔴 402 响应**不含**结构化 required/available（前端据此改用下界，而非解析英文 message）", async () => {
    const conv = await createConversation();
    const err = await sendMessage(conv.id, { content: "hi", tier: "low", attachment_asset_ids: [] }).catch((e) => e);
    expect(err.status).toBe(402);
    expect(err.detail).toBeUndefined();
    expect(err.message).toMatch(/required .*available/); // 两个数只存在于这句英文自由文本里
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
