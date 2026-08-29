import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { BillingSummary } from "@/lib/api/types";

import { BillingStatus } from "./billing-status";

const key = "11111111-1111-4111-8111-111111111111";

function summary(
  status: BillingSummary["status"],
  requested = 30
): BillingSummary {
  const amounts = {
    reserved: [requested, 0, 0],
    settled: [0, requested, 0],
    partially_settled: [0, 18, 12],
    released: [0, 0, requested]
  }[status];
  return {
    operation_id: "op-1",
    idempotency_key: key,
    status,
    requested_credits: requested,
    held_credits: amounts[0],
    settled_credits: amounts[1],
    released_credits: amounts[2]
  };
}

describe("BillingStatus", () => {
  it.each([
    [summary("reserved"), "已冻结 30 积分"],
    [summary("settled"), "已结算 30 积分"],
    [summary("partially_settled"), "部分结算 18 积分，已释放 12 积分"],
    [summary("released"), "未扣款，已释放 30 积分"]
  ] as const)("maps a parsed server summary to its financial wording", (value, copy) => {
    render(<BillingStatus summary={value} />);
    expect(screen.getByText(copy)).toBeInTheDocument();
  });

  it("distinguishes legal zero-price CosyVoice success from released failure", () => {
    const view = render(<BillingStatus summary={summary("settled", 0)} />);
    expect(screen.getByText("免费服务，已完成（未扣积分）")).toBeInTheDocument();
    view.rerender(<BillingStatus summary={summary("released")} />);
    expect(screen.getByText("未扣款，已释放 30 积分")).toBeInTheDocument();
  });

  it("fails closed to an unknown query state and exposes continue lookup", () => {
    const onContinueLookup = vi.fn();
    render(<BillingStatus summary={{ ...summary("settled"), settled_credits: 29 }} querying onContinueLookup={onContinueLookup} />);
    expect(screen.getByText("计费结果确认中")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "继续查询" }));
    expect(onContinueLookup).toHaveBeenCalledTimes(1);
  });
});
