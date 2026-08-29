import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const quota = vi.hoisted(() => ({ data: undefined as Record<string, unknown> | undefined }));
vi.mock("@/lib/api/hooks", () => ({ useQuota: () => ({ data: quota.data }) }));

import { QuotaBadge } from "./quota-badge";

describe("QuotaBadge", () => {
  it("separates manual holds and pending refunds from ordinary wallet reservations", () => {
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
    expect(screen.getByText("运行任务冻结 12")).toBeVisible();
    expect(screen.getByText("人工交付冻结 30000")).toBeVisible();
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
  });
});
