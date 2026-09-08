import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const orders = vi.hoisted(() => ({ data: [] as Array<Record<string, unknown>>, refetch: vi.fn() }));

vi.mock("@/lib/api/hooks", () => ({
  useQuota: () => ({ refetch: vi.fn().mockResolvedValue({ isError: false }) }),
  useBrandVoiceOrders: () => ({
    data: orders.data,
    isLoading: false,
    isError: false,
    refetch: orders.refetch
  })
}));

import { BrandVoiceOrderList } from "./brand-voice-order-list";

const billing = (status: "reserved" | "settled" | "released", amount: number) => ({
  operation_id: "op-1",
  idempotency_key: "00000000-0000-4000-8000-000000000001",
  status,
  requested_credits: amount,
  held_credits: status === "reserved" ? amount : 0,
  settled_credits: status === "settled" ? amount : 0,
  released_credits: status === "released" ? amount : 0
});

function order(overrides: Record<string, unknown> = {}) {
  return {
    id: "order-1",
    tenant_id: "tenant-1",
    ordered_by_user_id: "user-1",
    order_type: "create",
    requested_name: "主播音",
    source_audio_asset_id: "asset-1",
    existing_brand_voice_id: null,
    status: "awaiting_fulfillment",
    fulfilled_brand_voice_id: null,
    fulfilled_provider_voice_id: null,
    rejection_reason: null,
    fulfilled_at: null,
    rejected_at: null,
    created_at: "2026-08-30T01:00:00Z",
    updated_at: "2026-08-30T01:00:00Z",
    billing: billing("reserved", 30000),
    refund_disposition: "not_applicable",
    refund_grant_status: null,
    refund_applied_at: null,
    ...overrides
  };
}

beforeEach(() => {
  orders.data = [];
  orders.refetch.mockReset();
});

describe("BrandVoiceOrderList", () => {
  it("shows a manual non-cancellable hold and never pretends provider cloning", () => {
    orders.data = [order()];
    render(<BrandVoiceOrderList />);
    expect(screen.getByText("已冻结 30000 积分，等待平台人工交付；订单不自动超时且无法取消")).toBeVisible();
    expect(screen.queryByText("供应商生成中")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "取消订单" })).not.toBeInTheDocument();
  });

  it("renders fulfilled settlement and the server timestamp in the user's timezone", () => {
    orders.data = [order({
      status: "fulfilled",
      fulfilled_brand_voice_id: "voice-1",
      fulfilled_provider_voice_id: "provider-1",
      fulfilled_at: "2026-08-30T02:00:00Z",
      billing: billing("settled", 30000)
    })];
    render(<BrandVoiceOrderList />);
    expect(screen.getByText("已交付并结算 30000 积分")).toBeVisible();
    expect(screen.getByText(`交付时间：${new Date("2026-08-30T02:00:00Z").toLocaleString()}`)).toBeVisible();
  });

  it.each([
    ["source_subscription_released", null, "原订阅冻结已释放，积分现在可用"],
    ["current_subscription_credited", "applied", "已退回当前订阅，积分现在可用"],
    ["pending_next_subscription", "pending", "退款将在下次订阅激活时到账"],
    ["current_subscription_credited", "applied", "已退回当前订阅，积分现在可用"]
  ])("uses only refund disposition %s/%s", (disposition, grant, expected) => {
    orders.data = [order({
      status: "rejected",
      rejected_at: "2026-08-30T03:00:00Z",
      rejection_reason: "素材不合格",
      billing: billing("released", 30000),
      refund_disposition: disposition,
      refund_grant_status: grant,
      refund_applied_at: grant === "applied" ? "2026-08-30T04:00:00Z" : null
    })];
    render(<BrandVoiceOrderList />);
    expect(screen.getByText(expected)).toBeVisible();
  });

  it("can re-query a pending grant so later authoritative applied state is shown", () => {
    orders.data = [order({
      status: "rejected",
      rejected_at: "2026-08-30T03:00:00Z",
      rejection_reason: "素材不合格",
      billing: billing("released", 30000),
      refund_disposition: "pending_next_subscription",
      refund_grant_status: "pending"
    })];
    render(<BrandVoiceOrderList />);
    fireEvent.click(screen.getByRole("button", { name: "查询退款状态" }));
    expect(orders.refetch).toHaveBeenCalledTimes(1);
  });
});
