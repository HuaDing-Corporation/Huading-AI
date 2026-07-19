import { beforeEach, describe, expect, it } from "vitest";

import { createConversation, getConversation, sendMessage, topupWallet } from "@/lib/aibrain/api";
import type { SendMessageRequest } from "@/lib/aibrain/types";
import { resetAibrain } from "./aibrain-handlers";

// 🔴 mock 逐字段镜像 BE f2e9a2e0、**不比 BE 宽松**（含 402/422/502 状态码本身）。
beforeEach(() => resetAibrain());
const PROVIDER_FAIL = "__mock_provider_fail__"; // 与 mock 内约定一致

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
    await topupWallet({ amount: 100 });
    const conv = await createConversation();
    await expect(
      sendMessage(conv.id, { content: "x".repeat(8001), tier: "high", attachment_asset_ids: [] })
    ).rejects.toMatchObject({ status: 422, code: "AIBRAIN_REQUEST_LIMIT_EXCEEDED" });
  });

  it("上游失败 → 502 AIBRAIN_PROVIDER_FAILED（不落通用报错）", async () => {
    await topupWallet({ amount: 100 });
    const conv = await createConversation();
    await expect(
      sendMessage(conv.id, { content: PROVIDER_FAIL, tier: "low", attachment_asset_ids: [] })
    ).rejects.toMatchObject({ status: 502, code: "AIBRAIN_PROVIDER_FAILED" });
  });

  it("非法充值档位 → 422", async () => {
    await expect(topupWallet({ amount: 123 })).rejects.toMatchObject({ status: 422 });
  });

  it("未知会话 → 404 AIBRAIN_CONVERSATION_NOT_FOUND", async () => {
    await expect(getConversation("no-such-conv")).rejects.toMatchObject({
      status: 404,
      code: "AIBRAIN_CONVERSATION_NOT_FOUND"
    });
  });

  it("充值后发送 → 成功 + 结算 + 响应回填 wallet（available 扣典型消耗）", async () => {
    const w = await topupWallet({ amount: 500 }); // 0 + 500
    expect(w.available_credits).toBe(500);
    const conv = await createConversation();
    const res = await sendMessage(conv.id, { content: "你好", tier: "low", attachment_asset_ids: [] });
    expect(res.assistant_message.role).toBe("assistant");
    expect(res.assistant_message.status).toBe("completed");
    expect(res.wallet.available_credits).toBe(500 - 6); // low 典型 6
    expect(res.wallet.total_spent_credits).toBe(6);
  });

  it("🔴 会话切换不串数据：两个会话各自独立的消息", async () => {
    await topupWallet({ amount: 500 });
    const a = await createConversation();
    const b = await createConversation();
    await sendMessage(a.id, { content: "A的问题", tier: "low", attachment_asset_ids: [] });

    const detailA = await getConversation(a.id);
    const detailB = await getConversation(b.id);
    expect(detailA.messages.some((m) => m.content === "A的问题")).toBe(true);
    expect(detailB.messages).toHaveLength(0);
  });
});
