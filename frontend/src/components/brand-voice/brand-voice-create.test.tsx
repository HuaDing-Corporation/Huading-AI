import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

const createMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));

vi.mock("@/lib/api/hooks", () => ({
  useCreateBrandVoice: () => ({ mutateAsync: createMock.mutateAsync, isPending: createMock.isPending })
}));

// VIP 门禁：门禁唯一信号 = permissions 含 voice_clone_vip（不看 role）。默认授权（含该权限，doubao 可用）；
// VIP 置灰测试改 session 为无该权限。
const authMock = vi.hoisted(() => ({
  session: { role: "admin", user: { permissions: ["voice_clone_vip"] } } as { role: string; user?: { permissions: string[] } } | null,
  ready: true
}));
vi.mock("@/lib/auth/auth-context", () => ({ useAuth: () => authMock }));

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
  authMock.session = { role: "admin", user: { permissions: ["voice_clone_vip"] } }; // 每用例复位为 VIP 可用（含权限）
  authMock.ready = true;
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

  it("选 cosyvoice(免费)：直建、无扣费确认窗，提交带 provider:cosyvoice", async () => {
    const file = mp3();
    render(<BrandVoiceCreate />);
    uploadAudio(file);
    fireEvent.change(screen.getByLabelText(/音色名称/), { target: { value: "我的音" } });
    fireEvent.click(screen.getByRole("checkbox"));
    // 主动选免费档 cosyvoice（缺省是 doubao）。
    fireEvent.click(screen.getByText(copy.brandVoice.providerCosyTitle));
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.create }));

    // cosyvoice 免费 → 无扣费确认窗，直接创建；provider 进 body。
    expect(screen.queryByText(copy.brandVoice.chargeConfirmTitle)).not.toBeInTheDocument();
    await waitFor(() =>
      expect(createMock.mutateAsync).toHaveBeenCalledWith({
        name: "我的音",
        audio: file,
        consentConfirmed: true,
        provider: "cosyvoice"
      })
    );
  });

  // 承重·缺省 doubao（兼容承现状）：不选卡片时默认 doubao → 点创建即弹扣费确认（现状即豆包付费）。
  // ADMIN-VIP-GATE-UI-0001 §二之二：非 huading（且非 admin）→ doubao「升级版 VIP」置灰 + 提示（区别于「槽位空」）。
  it("VIP 门禁：非 huading（creator）→ doubao 卡置灰 + 「开通 huading plan 后可创建」（区别于「暂无可用音色槽位」）", () => {
    authMock.session = { role: "creator", user: { permissions: [] } };
    render(<BrandVoiceCreate />);
    expect(screen.getByText(copy.brandVoice.providerVipLocked)).toBeInTheDocument();
    // 置灰态：doubao 定价描述被锁定提示替换。
    expect(screen.queryByText(copy.brandVoice.providerDoubaoDesc)).not.toBeInTheDocument();
    // 免费档 cosyvoice 不受门禁，仍可见。
    expect(screen.getByText(copy.brandVoice.providerCosyTitle)).toBeInTheDocument();
  });

  it("VIP 门禁：非 huading 提交 → 缺省自动切 cosyvoice 免费直建（无扣费窗、provider:cosyvoice），绝不发 doubao", async () => {
    authMock.session = { role: "creator", user: { permissions: [] } };
    const file = mp3();
    render(<BrandVoiceCreate />);
    uploadAudio(file);
    fireEvent.change(screen.getByLabelText(/音色名称/), { target: { value: "免费音" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.create }));
    expect(screen.queryByText(copy.brandVoice.chargeConfirmTitle)).not.toBeInTheDocument();
    await waitFor(() =>
      expect(createMock.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ provider: "cosyvoice" }))
    );
  });

  // 承重·真实付费用户（Codex B P1 · FIX1）：creator + huading 套餐 → /me 已含 voice_clone_vip →
  // 「升级版 VIP」**不置灰、可选**，缺省 doubao 照走扣费确认，绝不被误伤为免费档。这条正是旧 mock 假绿掩盖的场景。
  it("VIP 门禁：creator + huading（permissions 含 voice_clone_vip）→ doubao 可选、无锁提示、走扣费确认（不误伤付费用户）", async () => {
    authMock.session = { role: "creator", user: { permissions: ["video:create", "voice_clone_vip"] } };
    const file = mp3();
    render(<BrandVoiceCreate />);
    // 无锁定提示；doubao 定价描述照常在（未被替换）。
    expect(screen.queryByText(copy.brandVoice.providerVipLocked)).not.toBeInTheDocument();
    expect(screen.getByText(copy.brandVoice.providerDoubaoDesc)).toBeInTheDocument();
    uploadAudio(file);
    fireEvent.change(screen.getByLabelText(/音色名称/), { target: { value: "付费音" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.create }));
    // 缺省 doubao 未被误切 cosyvoice → 弹扣费确认（付费通路可走）。
    expect(screen.getByText(copy.brandVoice.chargeConfirmTitle)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.chargeConfirmBtn }));
    await waitFor(() =>
      expect(createMock.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ provider: "doubao" }))
    );
  });

  // 管理员（默认）：doubao 可用，缺省仍走扣费确认（零回归）。
  it("缺省 doubao：不选卡片点创建 → 弹扣费确认，确认后带 provider:doubao", async () => {
    const file = mp3();
    render(<BrandVoiceCreate />);
    uploadAudio(file);
    fireEvent.change(screen.getByLabelText(/音色名称/), { target: { value: "默认音" } });
    fireEvent.click(screen.getByRole("checkbox"));
    // 不动通路卡（缺省 doubao）
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.create }));

    // 弹扣费确认窗 + 明确 30000 积分文案；此时**尚未**创建。
    expect(screen.getByText(copy.brandVoice.chargeConfirmTitle)).toBeInTheDocument();
    expect(screen.getByText(copy.brandVoice.chargeConfirmMessage(30000))).toBeInTheDocument();
    expect(createMock.mutateAsync).not.toHaveBeenCalled();
    // 确认扣费 → 带 provider:doubao 创建。
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.chargeConfirmBtn }));
    await waitFor(() =>
      expect(createMock.mutateAsync).toHaveBeenCalledWith({
        name: "默认音",
        audio: file,
        consentConfirmed: true,
        provider: "doubao"
      })
    );
  });

  it("doubao 扣费确认取消 → 不创建", () => {
    render(<BrandVoiceCreate />);
    uploadAudio();
    fireEvent.change(screen.getByLabelText(/音色名称/), { target: { value: "VIP音" } });
    fireEvent.click(screen.getByRole("checkbox"));
    // 缺省即 doubao，无需再点卡；点创建 → 弹扣费确认。
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.create }));
    // 取消
    fireEvent.click(screen.getByRole("button", { name: copy.common.cancel }));
    expect(createMock.mutateAsync).not.toHaveBeenCalled();
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

  it("名称 ≤30：超长输入截断到 30，提交 name 长度封顶 30（对齐后端 max_length=30）", async () => {
    const file = mp3();
    render(<BrandVoiceCreate />);
    uploadAudio(file);
    fireEvent.change(screen.getByLabelText(/音色名称/), { target: { value: "名".repeat(50) } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.create }));
    // 缺省 doubao → 过扣费确认再创建。
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.chargeConfirmBtn }));
    await waitFor(() => expect(createMock.mutateAsync).toHaveBeenCalled());
    expect(createMock.mutateAsync.mock.calls[0][0].name).toHaveLength(30);
  });

  it("防连点：创建中按钮显「创建中…」并禁用", () => {
    createMock.isPending = true;
    render(<BrandVoiceCreate />);
    expect(screen.getByRole("button", { name: copy.brandVoice.creating })).toBeDisabled();
  });
});
