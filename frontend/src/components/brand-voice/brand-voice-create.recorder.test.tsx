import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// 录音态依赖（error/durationSec/blob）需可控 → mock useAudioRecorder（上传路径整合见
// brand-voice-create.test.tsx 用真 hook）。
const recorderState = vi.hoisted(() => ({
  supported: true,
  recording: false,
  blob: null as Blob | null,
  url: null as string | null,
  durationSec: 0,
  error: null as string | null
}));
const api = vi.hoisted(() => ({
  upload: vi.fn(),
  estimateOrder: vi.fn(),
  createOrder: vi.fn(),
  estimateCosy: vi.fn(),
  createCosy: vi.fn()
}));

vi.mock("@/lib/media/use-audio-recorder", () => ({
  useAudioRecorder: () => ({ ...recorderState, start: vi.fn(), stop: vi.fn(), setExternal: vi.fn(), reset: vi.fn() })
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
// VIP 门禁（§二之二）：默认 admin（doubao 可用，录音用例零回归）。
vi.mock("@/lib/auth/auth-context", () => ({ useAuth: () => ({ session: { role: "admin", user: { permissions: ["voice_clone_vip"] } }, ready: true }) }));

import { BrandVoiceCreate } from "./brand-voice-create";

beforeEach(() => {
  recorderState.supported = true;
  recorderState.recording = false;
  recorderState.blob = null;
  recorderState.url = null;
  recorderState.durationSec = 0;
  recorderState.error = null;
  api.upload.mockReset().mockResolvedValue({ asset_id: "asset-recorder" });
  api.estimateOrder.mockReset().mockResolvedValue({
    pricing_contract: "billing_quote",
    operation: "doubao_brand_voice_order_create",
    pricing_shape: "simple",
    unit: "call",
    quantity: "1",
    unit_credits: "30000",
    rate_scope: "platform_fixed",
    rate_source: "fixed_policy",
    subtotal_credits: "30000",
    payable_credits: 30000,
    breakdown: [],
    disclosures: [],
    quote_token: "quote-recorder",
    expires_at: "2030-08-30T00:00:00Z"
  });
  api.createOrder.mockReset().mockImplementation((_input, confirmation) => Promise.resolve({
    id: "order-recorder",
    status: "awaiting_fulfillment",
    billing: {
      operation_id: "op-recorder",
      idempotency_key: confirmation.idempotency_key,
      status: "reserved",
      requested_credits: 30000,
      held_credits: 30000,
      settled_credits: 0,
      released_credits: 0
    }
  }));
  api.estimateCosy.mockReset();
  api.createCosy.mockReset();
});
afterEach(() => vi.clearAllMocks());

describe("BrandVoiceCreate · 录音态（mock useAudioRecorder）", () => {
  it("录音权限被拒/启动失败：组件渲染 recorder.error（role=alert，降级不断链）", () => {
    recorderState.error = copy.brandVoice.recordPermissionDenied;
    render(<BrandVoiceCreate />);
    expect(screen.getByText(copy.brandVoice.recordPermissionDenied)).toBeInTheDocument();
  });

  it("录音 <5s（tooShort）：出 audioTooShort + 创建禁用（不可发请求）", () => {
    recorderState.blob = new Blob(["x"], { type: "audio/webm" });
    recorderState.url = "blob:mock";
    recorderState.durationSec = 3;
    render(<BrandVoiceCreate />);
    expect(screen.getByText(copy.errors.audioTooShort)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交开通" })).toBeDisabled();
  });

  it("录音 ≥5s + 名称 + 授权：上传后提交人工订单（durationSec 不拦截）", async () => {
    const blob = new Blob(["x"], { type: "audio/webm" });
    recorderState.blob = blob;
    recorderState.url = "blob:mock";
    recorderState.durationSec = 8;
    render(<BrandVoiceCreate />);

    fireEvent.change(screen.getByLabelText(/音色名称/), { target: { value: "录音音色" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "提交开通" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认并提交人工开通" }));

    expect(api.upload).toHaveBeenCalledWith(blob);
    await waitFor(() => expect(api.createOrder).toHaveBeenCalledWith(
      expect.objectContaining({ requested_name: "录音音色", source_audio_asset_id: "asset-recorder", consent_confirmed: true, order_type: "create" }),
      expect.objectContaining({ quote_token: "quote-recorder" })
    ));
  });
});
