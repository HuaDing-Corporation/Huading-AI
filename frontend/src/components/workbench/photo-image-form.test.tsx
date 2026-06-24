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
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  uploadMock.mutateAsync.mockResolvedValue({ image_key: "uploads/ref.png" });
});
afterEach(() => vi.clearAllMocks());

function uploadReferenceImage() {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["x"], "r.png", { type: "image/png" })] } });
}

describe("PhotoImageForm (照片 / AI 图)", () => {
  it("disables 生成 with a hint until a prompt is entered", () => {
    render(<PhotoImageForm />);
    const generate = screen.getByRole("button", { name: /生成视频/ });
    expect(generate).toBeDisabled();
    expect(screen.getByText("请先输入提示词")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/描述想要的图片/), { target: { value: "一只橘猫" } });
    expect(generate).toBeEnabled();
  });

  it("文生图: submits the photo body without image_key when no reference image", async () => {
    render(<PhotoImageForm />);
    fireEvent.change(screen.getByPlaceholderText(/描述想要的图片/), { target: { value: "一只橘猫" } });
    fireEvent.click(screen.getByRole("button", { name: /生成视频/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    const [request] = taskMocks.createAndTrack.mock.calls[0];
    expect(request).toEqual({
      topic: "一只橘猫",
      video_mode: "photo",
      image_key: undefined,
      image_size: "1024x1024",
      image_quality: "medium"
    });
    // photo body never carries the video-only fields.
    expect(request).not.toHaveProperty("voice_id");
    expect(request).not.toHaveProperty("duration_sec");
  });

  it("修图: includes image_key when a reference image is uploaded", async () => {
    render(<PhotoImageForm />);
    fireEvent.change(screen.getByPlaceholderText(/描述想要的图片/), { target: { value: "把背景换成沙滩" } });
    uploadReferenceImage();
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(1));

    const generate = screen.getByRole("button", { name: /生成视频/ });
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
