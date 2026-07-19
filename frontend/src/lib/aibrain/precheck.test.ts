import { describe, expect, it } from "vitest";

import { precheckSend } from "./types";

// 发送前预检（对齐 BE 402 口径：预留 min(200,available)，available<=0 才是「压根发不了」）。
describe("precheckSend", () => {
  it("available > 0 → ok（细粒度不够 token 交给 BE 的 422）", () => {
    expect(precheckSend(1)).toEqual({ ok: true });
    expect(precheckSend(500)).toEqual({ ok: true });
  });

  it("🔴 available <= 0 → insufficient（「不发请求」的判据）", () => {
    expect(precheckSend(0)).toEqual({ ok: false, reason: "insufficient" });
    expect(precheckSend(-3)).toEqual({ ok: false, reason: "insufficient" });
  });
});
