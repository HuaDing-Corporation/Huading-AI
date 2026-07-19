import { beforeEach, describe, expect, it } from "vitest";

import { createConversation, getConversation, rechargeWallet, sendMessage } from "@/lib/aibrain/api";
import type { SendMessageRequest } from "@/lib/aibrain/types";
import { resetAibrain } from "./aibrain-handlers";

// 🔴 mock **不比 BE 宽松**：正确拒绝非法档位 / 余额不足 / 超单次上限 / 非法充值档位 / 未知会话。
// stores 模块级、server.resetHandlers 不清 → 每个用例 beforeEach 重置。
beforeEach(() => resetAibrain());

describe("aibrain mock 契约 · 正确拒绝（不比 BE 宽松）", () => {
  it("非法档位 → 422 INVALID_TIER", async () => {
    const conv = await createConversation();
    const body = { content: "hi", tier: "ultra" } as unknown as SendMessageRequest;
    await expect(sendMessage(conv.id, body)).rejects.toMatchObject({ status: 422, code: "INVALID_TIER" });
  });

  it("余额不足（seed 50 < mid reserve 100）→ 422 INSUFFICIENT_BALANCE", async () => {
    const conv = await createConversation();
    await expect(sendMessage(conv.id, { content: "hi", tier: "mid" })).rejects.toMatchObject({
      status: 422,
      code: "INSUFFICIENT_BALANCE"
    });
  });

  it("超单次上限（high 200 + 1 附件 = 230 > 200）→ 422 OVER_SINGLE_LIMIT（先于余额检查）", async () => {
    const conv = await createConversation();
    await expect(
      sendMessage(conv.id, { content: "hi", tier: "high", attachments: [{ kind: "image", ref: "k", name: "a.png" }] })
    ).rejects.toMatchObject({ status: 422, code: "OVER_SINGLE_LIMIT" });
  });

  it("非法充值档位（非 100/500/1000/2000）→ 422", async () => {
    await expect(rechargeWallet({ amount: 123 })).rejects.toMatchObject({ status: 422 });
  });

  it("未知会话 → 404 CONVERSATION_NOT_FOUND", async () => {
    await expect(getConversation("no-such-conv")).rejects.toMatchObject({
      status: 404,
      code: "CONVERSATION_NOT_FOUND"
    });
  });

  it("充值后低档发送 → 成功答复 + 按典型消耗结算（reserve→settle）", async () => {
    await rechargeWallet({ amount: 500 }); // 50 + 500 = 550
    const conv = await createConversation();
    const res = await sendMessage(conv.id, { content: "你好", tier: "low" });
    expect(res.assistant_message.role).toBe("assistant");
    expect(res.user_message.content).toBe("你好");
    expect(res.balance).toBe(550 - 6); // low 典型消耗 6（多退少补后的真实余额）
  });

  it("🔴 会话切换不串数据：两个会话各自独立的消息", async () => {
    await rechargeWallet({ amount: 500 });
    const a = await createConversation();
    const b = await createConversation();
    await sendMessage(a.id, { content: "A的问题", tier: "low" });

    const detailA = await getConversation(a.id);
    const detailB = await getConversation(b.id);
    expect(detailA.messages.some((m) => m.content === "A的问题")).toBe(true);
    expect(detailB.messages).toHaveLength(0); // B 没有 A 的消息
  });
});
