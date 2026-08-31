import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const hooks = vi.hoisted(() => ({
  data: [] as Array<Record<string, unknown>>,
  del: vi.fn()
}));
vi.mock("@/lib/api/hooks", () => ({
  useBrandVoices: () => ({ data: hooks.data, isLoading: false, isError: false }),
  useDeleteBrandVoice: () => ({ mutateAsync: hooks.del, isPending: false })
}));

import { BrandVoiceList } from "./brand-voice-list";

const voice = (
  id: string,
  name: string,
  delivery: string,
  expires_at: string | null = null,
  provider = "doubao-voice-clone",
  order_status: string | null = "fulfilled"
) => ({
  id,
  name,
  provider,
  status: "ready",
  order_status,
  delivery_status: delivery,
  expires_at,
  created_at: "2026-08-01T00:00:00Z"
});

beforeEach(() => {
  hooks.data = [];
  hooks.del.mockReset().mockResolvedValue({ deleted: true });
});

describe("BrandVoiceList", () => {
  it("renders server-authorized delivery status and exact expiry", () => {
    const expiry = "2026-09-01T00:00:00Z";
    hooks.data = [voice("active", "客户音", "active", expiry)];
    render(<BrandVoiceList />);
    expect(screen.getByText("可用")).toBeVisible();
    expect(screen.getByText(`到期时间：${new Date(expiry).toLocaleString()}`)).toBeVisible();
  });

  it("offers renewal only for an expired payer-visible voice", () => {
    const renew = vi.fn();
    hooks.data = [voice("active", "可用音", "active"), voice("expired", "过期音", "expired", "2026-08-01T00:00:00Z")];
    render(<BrandVoiceList onRenew={renew} />);
    expect(screen.getAllByRole("button", { name: "使用新音频续期" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "使用新音频续期" }));
    expect(renew).toHaveBeenCalledWith(expect.objectContaining({ id: "expired" }));
  });

  it.each([
    ["cosy", "cosyvoice-voice-clone", "fulfilled"],
    ["legacy", "doubao", "fulfilled"],
    ["official", "doubao-voice-clone", null],
    ["unowned", "doubao-voice-clone", null],
    ["unfulfilled", "doubao-voice-clone", "awaiting_fulfillment"]
  ])("does not offer renewal for an ineligible expired %s voice", (_kind, provider, orderStatus) => {
    hooks.data = [voice("expired", "不可续期音色", "expired", "2026-08-01T00:00:00Z", provider, orderStatus)];
    render(<BrandVoiceList onRenew={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "使用新音频续期" })).not.toBeInTheDocument();
  });

  it("does not promise a refund on delete", async () => {
    hooks.data = [voice("active", "客户音", "active")];
    render(<BrandVoiceList />);
    fireEvent.click(screen.getByRole("button", { name: "删除 客户音" }));
    expect(screen.getByText("删除只会移除该音色，不代表退款；任何退款均以人工订单返回状态为准。")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(hooks.del).toHaveBeenCalledWith("active"));
  });
});
