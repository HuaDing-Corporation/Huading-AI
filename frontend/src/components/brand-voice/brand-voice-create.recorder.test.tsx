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
const createMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));

vi.mock("@/lib/media/use-audio-recorder", () => ({
  useAudioRecorder: () => ({ ...recorderState, start: vi.fn(), stop: vi.fn(), setExternal: vi.fn(), reset: vi.fn() })
}));
vi.mock("@/lib/api/hooks", () => ({
  useCreateBrandVoice: () => ({ mutateAsync: createMock.mutateAsync, isPending: createMock.isPending })
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
  createMock.isPending = false;
  createMock.mutateAsync.mockResolvedValue({ id: "bv-1", name: "x", status: "processing", created_at: "" });
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
    expect(screen.getByRole("button", { name: copy.brandVoice.create })).toBeDisabled();
  });

  it("录音 ≥5s + 名称 + 授权：create 提交 {name, audio}（durationSec 不拦截）", async () => {
    const blob = new Blob(["x"], { type: "audio/webm" });
    recorderState.blob = blob;
    recorderState.url = "blob:mock";
    recorderState.durationSec = 8;
    render(<BrandVoiceCreate />);

    fireEvent.change(screen.getByLabelText(/音色名称/), { target: { value: "录音音色" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.create }));
    // 缺省 doubao → 过扣费确认再创建（带 provider:doubao）。
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.chargeConfirmBtn }));

    await waitFor(() =>
      expect(createMock.mutateAsync).toHaveBeenCalledWith({ name: "录音音色", audio: blob, consentConfirmed: true, provider: "doubao" })
    );
  });
});
