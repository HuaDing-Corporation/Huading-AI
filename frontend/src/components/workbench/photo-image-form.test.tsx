import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const taskMocks = vi.hoisted(() => ({ createAndTrack: vi.fn() }));
const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const estimateMock = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("@/lib/api/hooks", () => ({
  useUploadProductImage: () => ({ mutateAsync: uploadMock.mutateAsync, isPending: false }),
  useEstimateVideo: () => ({
    mutate: estimateMock.mutate,
    reset: vi.fn(),
    isPending: false,
    data: { estimated_credits: 8, unit: "credits" }
  })
}));
vi.mock("@/lib/videos/tasks-context", () => ({ useVideoTasks: () => taskMocks }));

import { PhotoImageForm } from "./photo-image-form";

beforeEach(() => {
  window.localStorage.clear(); // 每用例干净起点：AI 标识开关默认关
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  uploadMock.mutateAsync.mockResolvedValue({ image_key: "uploads/ref.png" });
});
afterEach(() => vi.clearAllMocks());

function uploadReferenceImage() {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["x"], "r.png", { type: "image/png" })] } });
}

describe("PhotoImageForm (图片生成 / 修改)", () => {
  it("disables 生成 with a hint until a prompt is entered", () => {
    render(<PhotoImageForm />);
    const generate = screen.getByRole("button", { name: /生成图片/ });
    expect(generate).toBeDisabled();
    expect(screen.getByText("请先输入提示词")).toBeInTheDocument();
    // module renamed → card title is 图片生成 / 修改; button verb is 生成图片 (queried above)
    expect(screen.getByText("图片生成 / 修改")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/描述想要的图片/), { target: { value: "一只橘猫" } });
    expect(generate).toBeEnabled();
  });

  it("文生图: 提交体带 aspect_ratio 默认 1:1、无 image_key；去质量/尺寸（IMAGE-ASPECT-RATIO-UI-0001）", async () => {
    render(<PhotoImageForm />);
    // 去掉「质量」下拉：不再有质量文案
    expect(screen.queryByText("质量")).not.toBeInTheDocument();
    expect(screen.getByText("画面比例")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/描述想要的图片/), { target: { value: "一只橘猫" } });
    fireEvent.click(screen.getByRole("button", { name: /生成图片/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    const [request] = taskMocks.createAndTrack.mock.calls[0];
    expect(request).toEqual({
      topic: "一只橘猫",
      video_mode: "photo",
      image_key: undefined,
      aspect_ratio: "1:1", // 默认 1:1
      apply_visible_label: false
    });
    // 不再带质量/尺寸；也不带 video-only 字段。
    expect(request).not.toHaveProperty("image_quality");
    expect(request).not.toHaveProperty("image_size");
    expect(request).not.toHaveProperty("voice_id");
    expect(request).not.toHaveProperty("duration_sec");
  });

  it("选画面比例 16:9 → 提交体 aspect_ratio:16:9（承重·选择接线）", async () => {
    render(<PhotoImageForm />);
    fireEvent.change(screen.getByPlaceholderText(/描述想要的图片/), { target: { value: "赛博城市" } });
    // 打开画面比例下拉，选 16:9（Radix Select 触发器 role=combobox）。
    fireEvent.click(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name: /16:9/ }));
    fireEvent.click(screen.getByRole("button", { name: /生成图片/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].aspect_ratio).toBe("16:9");
  });

  // LABEL-TOGGLE-UI-0001 承重：开启开关 → 提交体 apply_visible_label:true（锁 photo 面板接线）
  it("开启 AI 标识开关 → 提交体 apply_visible_label:true（承重）", async () => {
    render(<PhotoImageForm />);
    fireEvent.change(screen.getByPlaceholderText(/描述想要的图片/), { target: { value: "一只橘猫" } });
    fireEvent.click(screen.getByRole("switch")); // 开启 AI 生成标识
    fireEvent.click(screen.getByRole("button", { name: /生成图片/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].apply_visible_label).toBe(true);
  });

  it("修图: includes image_key when a reference image is uploaded", async () => {
    render(<PhotoImageForm />);
    fireEvent.change(screen.getByPlaceholderText(/描述想要的图片/), { target: { value: "把背景换成沙滩" } });
    uploadReferenceImage();
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(1));

    const generate = screen.getByRole("button", { name: /生成图片/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0]).toMatchObject({
      video_mode: "photo",
      image_key: "uploads/ref.png"
    });
  });
});
