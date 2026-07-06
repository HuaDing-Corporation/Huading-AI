import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// 关键回归：字幕样式「不选不传」（向后兼容铁律，不回归 0001）+ 选了并入 subtitle_style。
// 真实 NewVideoForm + 真实 SubtitleStylePicker，mock hooks 层(不真调后端)。

const taskMocks = vi.hoisted(() => ({ createAndTrack: vi.fn() }));
const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));

vi.mock("@/lib/api/hooks", () => ({
  useScriptGenerate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadImage: () => ({ mutateAsync: uploadMock.mutateAsync, isPending: false }),
  useBrandVoices: () => ({ data: [], isLoading: false }),
  useVoices: () => ({
    data: [{ id: "v1", provider: "edge", voice_code: "x", display_name: "音色1", gender: null, language: null }]
  }),
  useAvatarPresets: () => ({ data: [] }),
  useSubtitleTemplates: () => ({
    data: [
      { id: "classic", name: "经典白", font_family: "Noto Sans SC", font_size: 48, color: "#FFFFFF", stroke_color: "#000000", stroke_width: 2, background: null, position: "bottom" }
    ]
  }),
  useEstimateVideo: () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, data: { estimated_credits: 8, unit: "credits" } })
}));
vi.mock("@/lib/videos/tasks-context", () => ({ useVideoTasks: () => taskMocks }));

import { NewVideoForm } from "./new-video-form";

beforeEach(() => {
  window.localStorage.clear(); // 每用例干净起点：AI 标识开关默认关
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  uploadMock.mutateAsync.mockResolvedValue({ asset_id: "av-1" });
});
afterEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear(); // 清 AI 标识开关记忆，隔离用例
});

function uploadAvatar() {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["x"], "a.png", { type: "image/png" })] } });
}
async function fillRequired() {
  fireEvent.change(screen.getByPlaceholderText(/输入一句话主题/), { target: { value: "主题X" } });
  uploadAvatar();
  await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalled());
}
async function submit() {
  const generate = screen.getByRole("button", { name: /生成视频/ });
  await waitFor(() => expect(generate).toBeEnabled());
  fireEvent.click(generate);
  fireEvent.click(await screen.findByRole("button", { name: "确定" }));
  await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
}

describe("NewVideoForm 字幕样式 (ORAL-PROD-UI-0001)", () => {
  it("不选字幕样式 → 提交体不含 subtitle_style（不回归 0001）", async () => {
    render(<NewVideoForm />);
    await fillRequired();
    await submit();
    const [req] = taskMocks.createAndTrack.mock.calls[0];
    expect(req.subtitle_style).toBeUndefined();
    // 仍保留 0001 默认字段 + AI 标识默认关(LABEL-TOGGLE-UI-0001)
    expect(req).toMatchObject({ topic: "主题X", voice_id: "v1", avatar_asset_id: "av-1", subtitle_enabled: true });
    expect(req.apply_visible_label).toBe(false);
  });

  it("开启 AI 标识开关 → 提交体 apply_visible_label:true（承重）", async () => {
    render(<NewVideoForm />);
    await fillRequired();
    fireEvent.click(screen.getByRole("switch")); // 开启 AI 生成标识
    await submit();
    expect(taskMocks.createAndTrack.mock.calls[0][0].apply_visible_label).toBe(true);
  });

  it("选字幕预设 → 提交体并入 subtitle_style { template_id }", async () => {
    render(<NewVideoForm />);
    await fillRequired();
    fireEvent.click(screen.getByRole("button", { name: "经典白" }));
    await submit();
    expect(taskMocks.createAndTrack.mock.calls[0][0].subtitle_style).toEqual({ template_id: "classic" });
  });

  it("选预设后改字号覆盖 → subtitle_style 带 font_size", async () => {
    render(<NewVideoForm />);
    await fillRequired();
    fireEvent.click(screen.getByRole("button", { name: "经典白" }));
    fireEvent.change(screen.getByLabelText("字号"), { target: { value: "72" } });
    await submit();
    expect(taskMocks.createAndTrack.mock.calls[0][0].subtitle_style).toEqual({ template_id: "classic", font_size: 72 });
  });

  it("字幕字号越界 → 禁用生成（校验拦截非法覆盖）", async () => {
    render(<NewVideoForm />);
    await fillRequired();
    fireEvent.click(screen.getByRole("button", { name: "经典白" }));
    fireEvent.change(screen.getByLabelText("字号"), { target: { value: "999" } });
    expect(screen.getByRole("button", { name: /生成视频/ })).toBeDisabled();
  });
});
