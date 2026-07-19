import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// 🔴 §四之二 承重：充值幂等键的**前端生命周期**——重试复用同一 key（不双扣），改档位=新意图=新 key。
// mock 掉 useTopup，捕获每次 mutateAsync 收到的 idempotencyKey。
const calls: string[] = [];
let failNext = false;
vi.mock("@/lib/aibrain/hooks", () => ({
  useTopup: () => ({
    isPending: false,
    mutateAsync: async (vars: { amount: number; idempotencyKey: string }) => {
      calls.push(vars.idempotencyKey);
      if (failNext) {
        failNext = false;
        throw new Error("network");
      }
      return { available_credits: vars.amount };
    }
  })
}));

import { RechargeDialog } from "./recharge-dialog";

afterEach(() => {
  calls.length = 0;
  failNext = false;
  vi.clearAllMocks();
});

const clickConfirm = async () => {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "确认充值" }));
  });
};

describe("RechargeDialog · 充值幂等键生命周期", () => {
  it("🔴 上次失败后重试 → **复用同一个 key**（一次网络重试不双扣）", async () => {
    failNext = true;
    render(<RechargeDialog open onOpenChange={() => {}} />);
    await clickConfirm(); // 失败
    await clickConfirm(); // 重试
    expect(calls).toHaveLength(2);
    expect(calls[0]).toBeTruthy();
    expect(calls[0]).toBe(calls[1]); // 同一 key
  });

  it("🔴 改档位 = 新充值意图 → **新 key**", async () => {
    render(<RechargeDialog open onOpenChange={() => {}} />);
    await clickConfirm();
    fireEvent.click(screen.getByRole("radio", { name: "1000 积分" }));
    await clickConfirm();
    expect(calls).toHaveLength(2);
    expect(calls[0]).not.toBe(calls[1]); // 不同 key
  });
});
