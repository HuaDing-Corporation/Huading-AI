import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

const createMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));

vi.mock("@/lib/api/hooks", () => ({
  useCreateBrandVoice: () => ({ mutateAsync: createMock.mutateAsync, isPending: createMock.isPending })
}));

import { BrandVoiceCreate } from "./brand-voice-create";

const mp3 = (name = "a.mp3") => new File(["xxxxxx"], name, { type: "audio/mpeg" });

// 通过上传路径提供音频（jsdom 无 MediaRecorder；useAudioRecorder 走 supported=false + setExternal）。
function uploadAudio(file = mp3()) {
  fireEvent.change(document.querySelector("#brand-voice-audio")!, { target: { files: [file] } });
}

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  createMock.isPending = false;
  createMock.mutateAsync.mockResolvedValue({ id: "bv-1", name: "我的音", status: "processing", created_at: "" });
});
afterEach(() => vi.clearAllMocks());

describe("BrandVoiceCreate (品牌音色创建)", () => {
  it("授权未勾：点创建绝不发请求 + 内联授权错误（load-bearing）", async () => {
    render(<BrandVoiceCreate />);
    uploadAudio();
    fireEvent.change(screen.getByLabelText(/音色名称/), { target: { value: "我的音" } });
    // 不勾授权
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.create }));

    expect(createMock.mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByText(copy.brandVoice.consentRequired)).toBeInTheDocument();
  });

  it("授权已勾 + 名称 + 音频：create 提交 {name, audio}", async () => {
    const file = mp3();
    render(<BrandVoiceCreate />);
    uploadAudio(file);
    fireEvent.change(screen.getByLabelText(/音色名称/), { target: { value: "我的音" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.create }));

    await waitFor(() => expect(createMock.mutateAsync).toHaveBeenCalledWith({ name: "我的音", audio: file }));
  });

  it("无音频：提示先录制/上传，不发请求", () => {
    render(<BrandVoiceCreate />);
    fireEvent.change(screen.getByLabelText(/音色名称/), { target: { value: "我的音" } });
    fireEvent.click(screen.getByRole("checkbox"));
    // 无音频 → 按钮 disabled；即便点击也不发请求
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.create }));
    expect(createMock.mutateAsync).not.toHaveBeenCalled();
  });

  it("非法音频类型：提示且不载入音频", () => {
    render(<BrandVoiceCreate />);
    fireEvent.change(document.querySelector("#brand-voice-audio")!, {
      target: { files: [new File(["x"], "a.txt", { type: "text/plain" })] }
    });
    expect(screen.getByText(copy.errors.audioType)).toBeInTheDocument();
  });

  it("音频过大：>20MB 提示且不载入音频", () => {
    render(<BrandVoiceCreate />);
    const big = new File(["x"], "big.mp3", { type: "audio/mpeg" });
    Object.defineProperty(big, "size", { value: 21 * 1024 * 1024 });
    fireEvent.change(document.querySelector("#brand-voice-audio")!, { target: { files: [big] } });
    expect(screen.getByText(copy.errors.audioTooLarge)).toBeInTheDocument();
    // 未载入：无试听音频元素
    expect(screen.queryByLabelText(copy.brandVoice.previewAria)).not.toBeInTheDocument();
  });

  it("防连点：创建中按钮显「创建中…」并禁用", () => {
    createMock.isPending = true;
    render(<BrandVoiceCreate />);
    expect(screen.getByRole("button", { name: copy.brandVoice.creating })).toBeDisabled();
  });
});
