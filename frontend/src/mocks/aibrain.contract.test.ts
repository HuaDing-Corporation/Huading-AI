import { beforeEach, describe, expect, it } from "vitest";

import { createConversation, getConversation, getWallet, sendMessage, topupWallet } from "@/lib/aibrain/api";
import { uploadImage } from "@/lib/api/uploads";
import type { SendMessageRequest } from "@/lib/aibrain/types";
import { resetAibrain } from "./aibrain-handlers";
import { registerMockAsset } from "./asset-registry";

// 🔴 mock 逐字段镜像 BE f2e9a2e0、**不比 BE 宽松**（含 402/422/502 状态码本身 + 充值幂等）。
beforeEach(() => resetAibrain());
const PROVIDER_FAIL = "__mock_provider_fail__"; // 与 mock 内约定一致
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

  it("请求过大（预留 200 也不够）→ 422 AIBRAIN_REQUEST_LIMIT_EXCEEDED", async () => {
    await topup(100);
    const conv = await createConversation();
    await expect(
      sendMessage(conv.id, { content: "x".repeat(8001), tier: "high", attachment_asset_ids: [] })
    ).rejects.toMatchObject({ status: 422, code: "AIBRAIN_REQUEST_LIMIT_EXCEEDED" });
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

  it("🔴 FIX2 次序：同时超上限 + 上游标记 → **422 先于 502**（BE：预留不够时 provider 不被调用）", async () => {
    await topup(100);
    const conv = await createConversation();
    // content 既超长(→422) 又含上游失败标记(→502)：应得 422（BE 在调 provider 前就 422 rollback）。
    const content = PROVIDER_FAIL + "x".repeat(8001);
    await expect(sendMessage(conv.id, { content, tier: "high", attachment_asset_ids: [] })).rejects.toMatchObject({
      status: 422,
      code: "AIBRAIN_REQUEST_LIMIT_EXCEEDED"
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

  it("充值后发送 → 成功 + 结算 + 响应回填 wallet（available 扣典型消耗）", async () => {
    const w = await topup(500); // 0 + 500
    expect(w.available_credits).toBe(500);
    const conv = await createConversation();
    const res = await sendMessage(conv.id, { content: "你好", tier: "low", attachment_asset_ids: [] });
    expect(res.assistant_message.role).toBe("assistant");
    expect(res.assistant_message.status).toBe("completed");
    expect(res.wallet.available_credits).toBe(500 - 6); // low 典型 6
    expect(res.wallet.total_spent_credits).toBe(6);
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
