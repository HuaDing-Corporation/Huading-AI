import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// AVATAR-VIDEO-SOURCE-UI-0001：数字人形象源照片/视频二选一。真实 NewVideoForm + AvatarVideoPicker，
// mock hooks 层 + mock 视频预检（jsdom 无法解码视频，validateAvatarVideo 注入 null 走通过路径）。

const taskMocks = vi.hoisted(() => ({ createAndTrack: vi.fn() }));
const uploadImgMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const uploadVideoMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));

vi.mock("@/lib/api/hooks", () => ({
  useScriptGenerate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadImage: () => ({ mutateAsync: uploadImgMock.mutateAsync, isPending: false }),
  useUploadAvatarVideo: () => ({ mutateAsync: uploadVideoMock.mutateAsync, isPending: false }),
  useBrandVoices: () => ({ data: [], isLoading: false }),
  useVoices: () => ({ data: [{ id: "v1", provider: "edge", voice_code: "x", display_name: "音色1", gender: null, language: null }] }),
  useAvatarPresets: () => ({ data: [] }),
  useSubtitleTemplates: () => ({ data: [] }),
  useEstimateVideo: () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, data: { estimated_credits: 8, unit: "credits" } })
}));
vi.mock("@/lib/videos/tasks-context", () => ({ useVideoTasks: () => taskMocks }));
vi.mock("@/lib/auth/auth-context", () => ({ useAuth: () => ({ session: { role: "admin" }, ready: true }) }));
// 预检在 jsdom 无法真跑（无视频解码）；注入通过，专注验证「切换 + 二选一提交」接线（预检矩阵已由 avatar-video.test 锁）。
vi.mock("@/lib/media/avatar-video", () => ({
  validateAvatarVideo: vi.fn().mockResolvedValue(null),
  ALLOWED_AVATAR_VIDEO_TYPES: ["video/mp4"]
}));

import { NewVideoForm } from "./new-video-form";

beforeEach(() => {
  window.localStorage.clear();
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  uploadImgMock.mutateAsync.mockResolvedValue({ asset_id: "av-1" });
  uploadVideoMock.mutateAsync.mockResolvedValue({ asset_id: "vid-1" });
});
afterEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

const setTopic = () => fireEvent.change(screen.getByPlaceholderText(/输入一句话主题/), { target: { value: "主题X" } });
async function submitAndCapture() {
  const generate = screen.getByRole("button", { name: /生成视频/ });
  await waitFor(() => expect(generate).toBeEnabled());
  fireEvent.click(generate);
  fireEvent.click(await screen.findByRole("button", { name: "确定" }));
  await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
  return taskMocks.createAndTrack.mock.calls[0][0];
}

describe("NewVideoForm 形象源二选一（AVATAR-VIDEO-SOURCE-UI-0001）", () => {
  it("承重·照片默认零回归：不切来源 → 提交带 avatar_asset_id、**不带 avatar_video_asset_id**", async () => {
    render(<NewVideoForm />);
    setTopic();
    // 默认照片档：ImagePicker 文件输入上传形象
    fireEvent.change(document.querySelector('input[type="file"]')!, {
      target: { files: [new File(["x"], "a.png", { type: "image/png" })] }
    });
    await waitFor(() => expect(uploadImgMock.mutateAsync).toHaveBeenCalled());
    const body = await submitAndCapture();
    expect(body.avatar_asset_id).toBe("av-1");
    expect(body.avatar_video_asset_id).toBeUndefined();
  });

  it("视频源：切「本人出镜视频」→ 传视频 → 提交带 avatar_video_asset_id、**不带 avatar_asset_id**（互斥）", async () => {
    render(<NewVideoForm />);
    setTopic();
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.sourceVideo }));
    // 视频档：AvatarVideoPicker 文件输入（#avatar-video），预检注入通过 → 上传
    fireEvent.change(document.querySelector("#avatar-video")!, {
      target: { files: [new File(["x"], "v.mp4", { type: "video/mp4" })] }
    });
    await waitFor(() => expect(uploadVideoMock.mutateAsync).toHaveBeenCalled());
    const body = await submitAndCapture();
    expect(body.avatar_video_asset_id).toBe("vid-1");
    expect(body.avatar_asset_id).toBeUndefined();
  });

  it("切视频后未传视频 → 生成禁用（按当前来源校验，不误用照片值）", () => {
    render(<NewVideoForm />);
    setTopic();
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.sourceVideo }));
    // 视频未上传 → 生成禁用
    expect(screen.getByRole("button", { name: /生成视频/ })).toBeDisabled();
  });

  it("承重·切换清空另一源（Review P3）：传视频 → 切照片再切回视频 → 视频值已清、生成禁用（无残留武装态）", async () => {
    render(<NewVideoForm />);
    setTopic();
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.sourceVideo }));
    fireEvent.change(document.querySelector("#avatar-video")!, {
      target: { files: [new File(["x"], "v.mp4", { type: "video/mp4" })] }
    });
    await waitFor(() => expect(uploadVideoMock.mutateAsync).toHaveBeenCalled());
    // 切回照片（清空视频源）再切回视频 → 视频值已被清 → 生成禁用（不残留「已上传但预览丢失」的武装态）
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.sourcePhoto }));
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.sourceVideo }));
    expect(screen.getByRole("button", { name: /生成视频/ })).toBeDisabled();
  });
});
