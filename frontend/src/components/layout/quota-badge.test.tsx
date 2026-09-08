import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const quota = vi.hoisted(() => ({ data: undefined as Record<string, unknown> | undefined }));
vi.mock("@/lib/api/hooks", () => ({ useQuota: () => ({ data: quota.data }) }));

import { QuotaBadge } from "./quota-badge";

describe("QuotaBadge", () => {
  it("labels a manual-only subscription hold as an overlapping total, not two additive holds", () => {
    quota.data = {
      has_active_subscription: true,
      active_subscription_id: "sub-1",
      total: 100000,
      used: 381,
      reserved: 30000,
      remaining: 69619,
      manual_fulfillment_held_credits: 30000,
      pending_refund_credits: 0
    };
    render(<QuotaBadge />);
    expect(screen.getByText("当前订阅冻结总额 30000")).toBeVisible();
    expect(screen.getByText("人工交付冻结（跨订阅） 30000")).toBeVisible();
    expect(screen.getByText("人工交付冻结与当前订阅冻结可能重叠，不相加。")).toBeVisible();
    expect(screen.getByText("当前余额 69619/100000")).toBeVisible();
    expect(screen.queryByText(/运行任务冻结|60000/)).not.toBeInTheDocument();
  });

  it("keeps cross-subscription manual holds verbatim even when they exceed the current total", () => {
    quota.data = {
      has_active_subscription: true,
      active_subscription_id: "sub-1",
      total: 100,
      used: 20,
      reserved: 12,
      remaining: 68,
      manual_fulfillment_held_credits: 30000,
      pending_refund_credits: 400
    };
    render(<QuotaBadge />);
    expect(screen.getByText("当前订阅冻结总额 12")).toBeVisible();
    expect(screen.getByText("人工交付冻结（跨订阅） 30000")).toBeVisible();
    expect(screen.getByText("人工交付冻结与当前订阅冻结可能重叠，不相加。")).toBeVisible();
    expect(screen.queryByText(/运行任务冻结|-29988|30012/)).not.toBeInTheDocument();
    expect(screen.getByText("待下期到账 400")).toBeVisible();
    expect(screen.getByText("当前余额 68/100")).toBeVisible();
  });

  it("shows a zero current wallet without treating cross-period fields as the balance", () => {
    quota.data = {
      has_active_subscription: false,
      active_subscription_id: null,
      total: 0,
      used: 0,
      reserved: 0,
      remaining: 0,
      manual_fulfillment_held_credits: 30000,
      pending_refund_credits: 30000
    };
    render(<QuotaBadge />);
    expect(screen.getByText("当前余额 0/0")).toBeVisible();
    expect(screen.getByText("当前无生效订阅")).toBeVisible();
    expect(screen.getByText("当前订阅冻结总额 0")).toBeVisible();
    expect(screen.getByText("人工交付冻结（跨订阅） 30000")).toBeVisible();
    expect(screen.getByText("待下期到账 30000")).toBeVisible();
  });
});
