import { describe, expect, it } from "vitest";

import { precheckSend, SINGLE_TURN_LIMIT, TIERS, type ChatAttachment } from "./types";

// 发送前预检的纯逻辑承重（拦在开答前的依据）。
describe("precheckSend（发送前预检）", () => {
  it("余额 ≥ reserve → ok", () => {
    expect(precheckSend(100, "low")).toEqual({ ok: true, reserve: TIERS.low.reserve });
  });

  it("🔴 余额 < reserve → insufficient（「不发请求」的判据）", () => {
    expect(precheckSend(5, "mid")).toEqual({ ok: false, reason: "insufficient", reserve: TIERS.mid.reserve });
  });

  it("🔴 reserve 超单次上限 → over_limit（high 200 + 1 附件 = 230 > 200）", () => {
    const att: ChatAttachment[] = [{ kind: "image", ref: "k", name: "a.png" }];
    const r = precheckSend(1_000_000, "high", att);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("over_limit");
    expect(SINGLE_TURN_LIMIT).toBe(200);
  });
});
