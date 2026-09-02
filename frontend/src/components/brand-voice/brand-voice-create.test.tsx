import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  upload: vi.fn(),
  estimateOrder: vi.fn(),
  createOrder: vi.fn(),
  estimateCosy: vi.fn(),
  createCosy: vi.fn()
}));
vi.mock("@/lib/api/brand-voices", () => ({
  uploadAudio: api.upload,
  estimateBrandVoice: api.estimateCosy,
  createBrandVoice: api.createCosy
}));
vi.mock("@/lib/api/brand-voice-orders", () => ({
  estimateBrandVoiceOrder: api.estimateOrder,
  createBrandVoiceOrder: api.createOrder
}));
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: { user: { permissions: ["voice_clone_vip"] } }, ready: true })
}));

import { BrandVoiceCreate } from "./brand-voice-create";
import { brandVoiceKeys, brandVoiceOrderKeys, voicesKey } from "@/lib/api/keys";

function renderCreate(ui: React.ReactElement = <BrandVoiceCreate />, client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return {
    client,
    ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
  };
}

const quote = (operation: string, payable: number, disclosures: Array<Record<string, unknown>> = []) => ({
  pricing_contract: "billing_quote",
  operation,
  pricing_shape: "simple",
  unit: "voice",
  quantity: "1",
  unit_credits: String(payable),
  rate_scope: "platform_fixed",
  rate_source: "fixed_policy",
  subtotal_credits: String(payable),
  payable_credits: payable,
  breakdown: [],
  disclosures,
  quote_token: `quote-${operation}`,
  expires_at: "2030-08-30T00:00:00Z"
});
const summary = (status: "reserved" | "settled", amount: number, idempotencyKey = "00000000-0000-4000-8000-000000000001") => ({
  operation_id: "op-1",
  idempotency_key: idempotencyKey,
  status,
  requested_credits: amount,
  held_credits: status === "reserved" ? amount : 0,
  settled_credits: status === "settled" ? amount : 0,
  released_credits: 0
});

function fill(file = new File(["audio"], "voice.mp3", { type: "audio/mpeg" })) {
  fireEvent.change(document.querySelector("#brand-voice-audio")!, { target: { files: [file] } });
  fireEvent.change(screen.getByLabelText(/音色名称/), { target: { value: "客户主播音" } });
  fireEvent.click(screen.getByRole("checkbox"));
  return file;
}

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:voice");
  URL.revokeObjectURL = vi.fn();
  api.upload.mockReset().mockResolvedValue({ asset_id: "asset-1" });
  api.estimateOrder.mockReset().mockResolvedValue(quote("doubao_brand_voice_order_create", 30000));
  api.createOrder.mockReset().mockImplementation((_input, confirmation) => Promise.resolve({
    id: "order-1",
    status: "awaiting_fulfillment",
    billing: summary("reserved", 30000, confirmation.idempotency_key)
  }));
  const disclosure = {
    key: "cosyvoice_tts_reference_rate",
    rendered_text: "创建免费；使用该音色时当前参考费率为 0.2 积分/字，实际使用时重新报价。",
    copy_version: 1,
    unit: "character",
    rate_scope: "tenant_overridable",
    rate_source: "tenant_rate",
    rate_id: "rate-tenant",
    effective_at: "2026-08-30T00:00:00Z",
    policy_key: null,
    policy_version: null,
    reference_unit_credits: "0.2"
  };
  api.estimateCosy.mockReset().mockResolvedValue(quote("cosyvoice_brand_voice_create", 0, [disclosure]));
  api.createCosy.mockReset().mockImplementation((_input, confirmation) => Promise.resolve({
    id: "voice-1",
    name: "客户主播音",
    provider: "cosyvoice-voice-clone",
    status: "ready",
    order_status: null,
    delivery_status: "active",
    expires_at: null,
    created_at: "2026-08-30T00:00:00Z",
    billing: summary("settled", 0, confirmation.idempotency_key)
  }));
});

describe("BrandVoiceCreate", () => {
  it("shows manual delivery instead of pretending Doubao is cloning", async () => {
    renderCreate();
    expect(screen.getByText("升级版 VIP 人工交付音色")).toBeVisible();
    expect(screen.getByText("提交人工订单，由平台交付；交付后有效 365 天")).toBeVisible();
    expect(screen.queryByText(/永久/)).not.toBeInTheDocument();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "提交开通" }));
    expect(await screen.findByText("本次冻结 30000 积分")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认并提交人工开通" }));
    expect(await screen.findByText("已冻结 30000 积分，等待平台人工交付；订单不自动超时且无法取消")).toBeVisible();
    expect(screen.queryByText("供应商生成中")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "取消订单" })).not.toBeInTheDocument();
  });

  it("shows free CosyVoice creation as success and renders the tenant quote disclosure verbatim", async () => {
    renderCreate();
    fill();
    fireEvent.click(screen.getByText("免费开通私人专属音色"));
    fireEvent.click(screen.getByRole("button", { name: "提交开通" }));
    expect(await screen.findByText("创建免费；使用该音色时当前参考费率为 0.2 积分/字，实际使用时重新报价。")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认并创建" }));
    expect(await screen.findByText("创建成功，本次创建免费（扣除 0 积分）")).toBeVisible();
    expect(screen.queryByText(/冻结已释放/)).not.toBeInTheDocument();
  });

  it("does not submit when no valid quote was obtained", async () => {
    api.estimateOrder.mockRejectedValue(new Error("quote unavailable"));
    renderCreate();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "提交开通" }));
    const confirm = await screen.findByRole("button", { name: "确认并提交人工开通" });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(api.createOrder).not.toHaveBeenCalled());
  });

  it("renewal requires a new audio upload and sends the expired voice id", async () => {
    api.estimateOrder.mockResolvedValue(quote("doubao_brand_voice_order_renew", 30000));
    renderCreate(<BrandVoiceCreate renewVoice={{
      id: "expired-1",
      name: "过期音",
      provider: "doubao-voice-clone",
      status: "ready",
      order_status: "fulfilled",
      delivery_status: "expired",
      expires_at: "2026-08-01T00:00:00Z",
      created_at: "2025-08-01T00:00:00Z"
    }} />);
    const file = new File(["fresh"], "fresh.mp3", { type: "audio/mpeg" });
    fireEvent.change(document.querySelector("#brand-voice-audio")!, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "提交续期" }));
    await screen.findByText("本次冻结 30000 积分");
    expect(api.estimateOrder).toHaveBeenCalledWith(expect.objectContaining({ order_type: "renew", existing_brand_voice_id: "expired-1", source_audio_asset_id: "asset-1" }));
  });

  it("invalidates each adjacent order, brand-voice, and voice-picker cache exactly once after one success", async () => {
    const orderRefresh = vi.fn().mockResolvedValue([]);
    const brandRefresh = vi.fn().mockResolvedValue([]);
    const pickerRefresh = vi.fn().mockResolvedValue([]);
    function AdjacentConsumers() {
      useQuery({ queryKey: brandVoiceOrderKeys.list(), queryFn: orderRefresh });
      useQuery({ queryKey: brandVoiceKeys.list(), queryFn: brandRefresh });
      useQuery({ queryKey: voicesKey, queryFn: pickerRefresh });
      return null;
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const view = renderCreate(
      <><AdjacentConsumers /><BrandVoiceCreate /></>,
      client
    );
    await waitFor(() => {
      expect(orderRefresh).toHaveBeenCalledTimes(1);
      expect(brandRefresh).toHaveBeenCalledTimes(1);
      expect(pickerRefresh).toHaveBeenCalledTimes(1);
    });

    fill();
    fireEvent.click(screen.getByRole("button", { name: "提交开通" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认并提交人工开通" }));
    await screen.findByText("已冻结 30000 积分，等待平台人工交付；订单不自动超时且无法取消");
    await waitFor(() => {
      expect(orderRefresh).toHaveBeenCalledTimes(2);
      expect(brandRefresh).toHaveBeenCalledTimes(2);
      expect(pickerRefresh).toHaveBeenCalledTimes(2);
    });
    expect(invalidate.mock.calls.map(([options]) => options?.queryKey)).toEqual([
      brandVoiceOrderKeys.all,
      brandVoiceKeys.all,
      voicesKey
    ]);

    view.rerender(
      <QueryClientProvider client={client}><AdjacentConsumers /><BrandVoiceCreate /></QueryClientProvider>
    );
    await Promise.resolve();
    expect(invalidate).toHaveBeenCalledTimes(3);
  });

  it("invalidates the same three caches exactly once after one CosyVoice success", async () => {
    let resolveEstimate!: (value: ReturnType<typeof quote>) => void;
    api.estimateCosy.mockImplementationOnce(() => new Promise<ReturnType<typeof quote>>((resolve) => {
      resolveEstimate = resolve;
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    renderCreate(<BrandVoiceCreate />, client);
    fill();
    fireEvent.click(screen.getByText("免费开通私人专属音色"));
    fireEvent.click(screen.getByRole("button", { name: "提交开通" }));
    const confirm = await screen.findByRole("button", { name: "确认并创建" });
    resolveEstimate(quote("cosyvoice_brand_voice_create", 0));
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(confirm);
    await screen.findByText("创建成功，本次创建免费（扣除 0 积分）");
    expect(invalidate.mock.calls.map(([options]) => options?.queryKey)).toEqual([
      brandVoiceOrderKeys.all,
      brandVoiceKeys.all,
      voicesKey
    ]);
  });
});
