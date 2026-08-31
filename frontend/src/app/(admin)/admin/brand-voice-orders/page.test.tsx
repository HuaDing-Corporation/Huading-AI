import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  detail: vi.fn(),
  resolve: vi.fn()
}));

vi.mock("@/lib/api/admin-console", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/admin-console")>("@/lib/api/admin-console");
  return {
    ...actual,
    listAdminBrandVoiceOrders: api.list,
    getAdminBrandVoiceOrder: api.detail,
    resolveAdminBrandVoiceOrder: api.resolve
  };
});

import AdminBrandVoiceOrdersPage from "./page";

const billing = {
  operation_id: "op-1",
  idempotency_key: "00000000-0000-4000-8000-000000000001",
  status: "reserved",
  requested_credits: 30000,
  held_credits: 30000,
  settled_credits: 0,
  released_credits: 0
};
const awaiting = {
  id: "order-1",
  tenant_id: "tenant-1",
  ordered_by_user_id: "user-1",
  order_type: "create",
  requested_name: "客户主播音",
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
  billing,
  refund_disposition: "not_applicable",
  refund_grant_status: null,
  refund_applied_at: null
};

beforeEach(() => {
  api.list.mockReset().mockResolvedValue({ items: [awaiting], total: 1, page: 1, page_size: 20 });
  api.detail.mockReset().mockResolvedValue({ ...awaiting, source_audio_url: "https://signed.test/audio.mp3" });
  api.resolve.mockReset().mockResolvedValue({
    ...awaiting,
    status: "fulfilled",
    fulfilled_brand_voice_id: "voice-1",
    fulfilled_provider_voice_id: "customer-voice-001",
    fulfilled_at: "2026-08-30T02:00:00Z",
    billing: { ...billing, status: "settled", held_credits: 0, settled_credits: 30000 }
  });
});

describe("AdminBrandVoiceOrdersPage", () => {
  it("loads signed audio only on detail and resolves one order once", async () => {
    render(<AdminBrandVoiceOrdersPage />);
    expect(await screen.findByText("客户主播音")).toBeVisible();
    expect(screen.getAllByText("等待人工交付")).toHaveLength(2);
    expect(screen.queryByText("awaiting_fulfillment")).not.toBeInTheDocument();
    const createdAt = document.querySelector("time");
    expect(createdAt).toHaveClass("whitespace-nowrap", "tabular-nums");
    expect(createdAt?.parentElement).toHaveClass("flex-col", "sm:flex-row");
    expect(api.detail).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "查看订单" }));
    expect(await screen.findByLabelText("订单源音频")).toHaveAttribute("src", "https://signed.test/audio.mp3");
    fireEvent.change(screen.getByLabelText("豆包音色 ID"), { target: { value: "customer-voice-001" } });
    fireEvent.click(screen.getByRole("button", { name: "确认交付" }));
    expect(await screen.findByText("已交付并结算 30000 积分")).toBeVisible();
    expect(screen.getByRole("button", { name: "确认交付" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "拒绝订单" })).toBeDisabled();
    expect(api.resolve).toHaveBeenCalledWith("order-1", { action: "fulfill", provider_voice_id: "customer-voice-001" });
  });

  it("validates mutually exclusive fulfill and reject payloads", async () => {
    render(<AdminBrandVoiceOrdersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "查看订单" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认交付" }));
    expect(screen.getByRole("alert")).toHaveTextContent("请输入豆包音色 ID");
    fireEvent.change(screen.getByLabelText("拒绝原因"), { target: { value: "素材授权不完整" } });
    fireEvent.click(screen.getByRole("button", { name: "拒绝订单" }));
    await waitFor(() => expect(api.resolve).toHaveBeenCalledWith("order-1", { action: "reject", rejection_reason: "素材授权不完整" }));
  });

  it("shows platform-admin 403 without making a billing claim", async () => {
    api.list.mockRejectedValue(Object.assign(new Error("仅平台管理员可访问"), { status: 403, code: "PLATFORM_ADMIN_REQUIRED" }));
    render(<AdminBrandVoiceOrdersPage />);
    expect(await screen.findByText("仅平台管理员可访问人工音色订单")).toBeVisible();
    expect(screen.queryByText(/未扣费|未扣款/)).not.toBeInTheDocument();
  });
});
