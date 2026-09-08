import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";

vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: { token: "test", tenantId: "ten-mock" }, ready: true })
}));

import { resetAdminConsole } from "@/mocks/handlers";
import { server } from "@/mocks/server";
import { apiUrl } from "@/lib/api/client";
import type { BrandVoiceOrderRead } from "@/lib/api/brand-voice-orders";
import { makeQueryClient } from "@/lib/query/client";
import { QuotaBadge } from "@/components/layout/quota-badge";
import { BrandVoiceOrderList } from "./brand-voice-order-list";

beforeEach(() => resetAdminConsole());

function orderSnapshot(rejected: boolean): BrandVoiceOrderRead {
  const base = {
    id: "order-refund",
    tenant_id: "ten-mock",
    ordered_by_user_id: "u-mock",
    order_type: "create" as const,
    requested_name: "退款同步测试音色",
    source_audio_asset_id: "asset-refund",
    existing_brand_voice_id: null,
    fulfilled_brand_voice_id: null,
    fulfilled_provider_voice_id: null,
    fulfilled_at: null,
    expires_at: null,
    created_at: "2026-09-08T00:00:00Z",
    updated_at: "2026-09-08T00:01:00Z",
    refund_grant_status: null,
    refund_applied_at: null,
    billing: {
      operation_id: "op-refund",
      idempotency_key: "00000000-0000-4000-8000-000000000001",
      requested_credits: 30000,
      settled_credits: 0
    }
  };
  return rejected ? {
    ...base,
    status: "rejected",
    rejection_reason: "拒单验收",
    rejected_at: "2026-09-08T00:01:00Z",
    refund_disposition: "source_subscription_released",
    billing: { ...base.billing, status: "released", held_credits: 0, released_credits: 30000 }
  } : {
    ...base,
    status: "awaiting_fulfillment",
    rejection_reason: null,
    rejected_at: null,
    refund_disposition: "not_applicable",
    billing: { ...base.billing, status: "reserved", held_credits: 30000, released_credits: 0 }
  };
}

function refundServer() {
  const state = { rejected: false, quotaFails: false };
  server.use(
    http.get(apiUrl("/api/v1/brand-voice-orders"), () => HttpResponse.json({
      data: { items: [orderSnapshot(state.rejected)], total: 1, page: null, page_size: null },
      error: null,
      request_id: "refund-test"
    })),
    http.get(apiUrl("/api/v1/quota"), () => state.quotaFails ? HttpResponse.error() : HttpResponse.json({
      data: {
        has_active_subscription: true,
        active_subscription_id: "sub-refund",
        total: 100000,
        // Concurrent usage means the new balance is NOT simply old balance + refund.
        used: state.rejected ? 398 : 381,
        reserved: state.rejected ? 0 : 30000,
        remaining: state.rejected ? 99602 : 69619,
        manual_fulfillment_held_credits: state.rejected ? 0 : 30000,
        pending_refund_credits: 0
      },
      error: null,
      request_id: "quota-test"
    }))
  );
  return state;
}

function renderOwner() {
  // Keep production freshness settings: cached quota is still fresh on this click.
  const client = makeQueryClient();
  client.setDefaultOptions({ queries: { ...client.getDefaultOptions().queries, retry: false } });
  return render(<QueryClientProvider client={client}><QuotaBadge /><BrandVoiceOrderList /></QueryClientProvider>);
}

describe("BrandVoiceOrderList authoritative refund refresh", () => {
  it("retains the last authoritative balance and warns on quota failure, then clears the warning on retry", async () => {
    const state = refundServer();
    renderOwner();
    await screen.findByText("当前余额 69619/100000");
    await screen.findByText("退款同步测试音色");
    state.rejected = true;
    state.quotaFails = true;

    fireEvent.click(screen.getByRole("button", { name: "查询退款状态" }));

    expect(await screen.findByText("原订阅冻结已释放，积分现在可用")).toBeVisible();
    expect(await screen.findByRole("alert")).toHaveTextContent("余额暂未同步");
    expect(screen.getByRole("alert")).toHaveTextContent("请重试查询退款状态");
    expect(screen.getByText("当前余额 69619/100000")).toBeVisible();
    expect(screen.getByText("当前订阅冻结总额 30000")).toBeVisible();
    expect(screen.getByText("人工交付冻结（跨订阅） 30000")).toBeVisible();
    expect(screen.queryByText(/当前余额 (99619|99602|0)\//)).not.toBeInTheDocument();

    state.quotaFails = false;
    fireEvent.click(screen.getByRole("button", { name: "查询退款状态" }));
    expect(await screen.findByText("当前余额 99602/100000")).toBeVisible();
    expect(screen.getByText("当前订阅冻结总额 0")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("refreshes the owner quota from the server when querying a rejected order's refund", async () => {
    const state = refundServer();
    renderOwner();
    expect(await screen.findByText("当前余额 69619/100000")).toBeVisible();
    await screen.findByText("退款同步测试音色");
    state.rejected = true;

    fireEvent.click(screen.getByRole("button", { name: "查询退款状态" }));

    expect(await screen.findByText("原订阅冻结已释放，积分现在可用")).toBeVisible();
    expect(await screen.findByText("当前余额 99602/100000")).toBeVisible();
    expect(screen.getByText("当前订阅冻结总额 0")).toBeVisible();
    expect(screen.getByText("人工交付冻结（跨订阅） 0")).toBeVisible();
    expect(screen.queryByText("当前余额 99619/100000")).not.toBeInTheDocument();
  });

  it("changes pending refund copy to currently credited after the user refreshes", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><BrandVoiceOrderList /></QueryClientProvider>);
    const pendingOrder = (await screen.findByText("待下期补回")).closest("li");
    expect(pendingOrder).not.toBeNull();
    expect(within(pendingOrder as HTMLElement).getByText("退款将在下次订阅激活时到账")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "查询退款状态" }));

    expect(await within(pendingOrder as HTMLElement).findByText("已退回当前订阅，积分现在可用")).toBeVisible();
    expect(within(pendingOrder as HTMLElement).queryByText("退款将在下次订阅激活时到账")).not.toBeInTheDocument();
  });
});
