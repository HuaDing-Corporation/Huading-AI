import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// 封面双 tab 流程（真实 FrameCoverTab / AiCoverTab 子组件，避开 jsdom 中不可靠的 Radix
// tab 激活），mock hooks 层(不真调后端)。AiCoverTab 用 useQueryClient → 包 QueryClientProvider。

const framesMock = vi.hoisted(() => ({ get: vi.fn() }));
const coverMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const createMock = vi.hoisted(() => ({ fn: vi.fn() }));
const tasksMock = vi.hoisted(() => ({ tasks: [] as Array<Record<string, unknown>> }));

vi.mock("@/lib/api/hooks", () => ({
  useFrameCandidates: () => framesMock.get(),
  useCoverFromFrame: () => ({ mutateAsync: coverMock.mutateAsync, isPending: false })
}));
vi.mock("@/lib/videos/tasks-context", () => ({
  useVideoTasks: () => ({ createAndTrack: createMock.fn, tasks: tasksMock.tasks })
}));

import { AiCoverTab, FrameCoverTab } from "./cover-panel";

function renderWithClient(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  tasksMock.tasks = [];
  framesMock.get.mockReturnValue({
    data: [
      { timestamp_sec: 0, preview_url: "https://mock.local/f0.jpg" },
      { timestamp_sec: 1.5, preview_url: "https://mock.local/f1.jpg" }
    ],
    isLoading: false,
    isError: false,
    refetch: vi.fn()
  });
  coverMock.mutateAsync.mockResolvedValue({ cover: { id: "c1", image_url: "https://mock.local/c.png", width: 1280, height: 720 } });
  createMock.fn.mockResolvedValue("task-cover-1");
});
afterEach(() => vi.clearAllMocks());

describe("CoverPanel · 截帧 tab (FrameCoverTab)", () => {
  it("未选帧时禁用生成", () => {
    renderWithClient(<FrameCoverTab videoTaskId="vt-1" />);
    expect(screen.getByRole("button", { name: /生成封面/ })).toBeDisabled();
  });

  it("选帧 + 标题 → createCoverFromFrame，预览出图", async () => {
    renderWithClient(<FrameCoverTab videoTaskId="vt-1" />);
    fireEvent.click(screen.getByText("0.0s"));
    fireEvent.change(screen.getByPlaceholderText(/输入封面标题/), { target: { value: "我的标题" } });
    fireEvent.click(screen.getByRole("button", { name: /生成封面/ }));

    await waitFor(() => expect(coverMock.mutateAsync).toHaveBeenCalledTimes(1));
    expect(coverMock.mutateAsync.mock.calls[0][0]).toMatchObject({
      video_task_id: "vt-1",
      timestamp_sec: 0,
      title: { text: "我的标题" }
    });
    expect(await screen.findByAltText("封面预览")).toBeInTheDocument();
  });

  it("纯截帧(标题留空)→ title.text 为空串", async () => {
    renderWithClient(<FrameCoverTab videoTaskId="vt-1" />);
    fireEvent.click(screen.getByText("0.0s"));
    fireEvent.click(screen.getByRole("button", { name: /生成封面/ }));
    await waitFor(() => expect(coverMock.mutateAsync).toHaveBeenCalledTimes(1));
    expect(coverMock.mutateAsync.mock.calls[0][0].title.text).toBe("");
  });

  it("候选帧加载失败 → 错误态", () => {
    framesMock.get.mockReturnValue({ data: undefined, isLoading: false, isError: true, refetch: vi.fn() });
    renderWithClient(<FrameCoverTab videoTaskId="vt-1" />);
    expect(screen.getByText("候选帧加载失败，请重试")).toBeInTheDocument();
  });
});

describe("CoverPanel · AI tab (AiCoverTab)", () => {
  it("空 prompt 时禁用生成", () => {
    renderWithClient(<AiCoverTab />);
    expect(screen.getByRole("button", { name: /AI 生成封面/ })).toBeDisabled();
  });

  it("prompt → createAndTrack(video_mode=photo, purpose=cover)", async () => {
    renderWithClient(<AiCoverTab />);
    fireEvent.change(screen.getByPlaceholderText(/描述想要的封面/), { target: { value: "咖啡杯特写" } });
    fireEvent.click(screen.getByRole("button", { name: /AI 生成封面/ }));

    await waitFor(() => expect(createMock.fn).toHaveBeenCalledTimes(1));
    expect(createMock.fn.mock.calls[0][0]).toMatchObject({
      topic: "咖啡杯特写",
      video_mode: "photo",
      purpose: "cover"
    });
  });

  it("任务 done + playbackUrl → 预览封面图", async () => {
    tasksMock.tasks = [
      { taskId: "task-cover-1", topic: "咖啡杯", status: "done", progress: 100, statusLabel: "已完成", playbackUrl: "https://mock.local/cover.png", downloadUrl: "https://mock.local/cover.png?dl=1" }
    ];
    renderWithClient(<AiCoverTab />);
    fireEvent.change(screen.getByPlaceholderText(/描述想要的封面/), { target: { value: "咖啡杯" } });
    fireEvent.click(screen.getByRole("button", { name: /AI 生成封面/ }));
    expect(await screen.findByAltText("封面预览")).toBeInTheDocument();
  });

  it("任务 failed → 友好错误态(friendlyImageError)", async () => {
    tasksMock.tasks = [
      { taskId: "task-cover-1", topic: "咖啡杯", status: "failed", progress: 0, statusLabel: "失败", errorCode: "IMAGE_GEN_FAILED" }
    ];
    renderWithClient(<AiCoverTab />);
    fireEvent.change(screen.getByPlaceholderText(/描述想要的封面/), { target: { value: "咖啡杯" } });
    fireEvent.click(screen.getByRole("button", { name: /AI 生成封面/ }));
    expect(await screen.findByText("图片生成失败，请重试")).toBeInTheDocument();
  });

  it("任务 done 但无 playbackUrl → 兜底提示(不静默死路)", async () => {
    tasksMock.tasks = [
      { taskId: "task-cover-1", topic: "咖啡杯", status: "done", progress: 100, statusLabel: "已完成", playbackUrl: null }
    ];
    renderWithClient(<AiCoverTab />);
    fireEvent.change(screen.getByPlaceholderText(/描述想要的封面/), { target: { value: "咖啡杯" } });
    fireEvent.click(screen.getByRole("button", { name: /AI 生成封面/ }));
    expect(await screen.findByText("已生成，请到图片历史查看")).toBeInTheDocument();
  });
});
