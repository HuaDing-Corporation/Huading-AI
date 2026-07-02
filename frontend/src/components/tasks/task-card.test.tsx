import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { TrackedTask } from "@/lib/sse/progress-mapping";
import { copy } from "@/lib/copy";

import { TaskCard } from "./task-card";

const failed: TrackedTask = {
  taskId: "t1",
  topic: "T",
  status: "failed",
  progress: 0,
  statusLabel: "失败",
  error: "boom"
};

describe("TaskCard retry visibility (P2-1)", () => {
  it("hides retry and shows a refill hint for a hydrated failed task (not retryable)", () => {
    const onRetry = vi.fn();
    render(<TaskCard task={failed} onOpen={vi.fn()} onRetry={onRetry} onUrlError={vi.fn()} />);
    expect(screen.queryByText("重试")).toBeNull();
    expect(screen.getByText("请到工作台重新发起")).toBeTruthy();
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("shows retry for a retryable failed task and calls onRetry", () => {
    const onRetry = vi.fn();
    render(
      <TaskCard task={{ ...failed, retryable: true }} onOpen={vi.fn()} onRetry={onRetry} onUrlError={vi.fn()} />
    );
    fireEvent.click(screen.getByText("重试"));
    expect(onRetry).toHaveBeenCalledWith("t1");
  });
});

describe("TaskCard inline player onError once (P2-2)", () => {
  it("fires onUrlError at most once across repeated errors", () => {
    const onUrlError = vi.fn();
    const done: TrackedTask = {
      taskId: "d1",
      topic: "T",
      status: "done",
      progress: 100,
      statusLabel: "已完成",
      playbackUrl: "https://example.test/v.mp4"
    };
    const { container } = render(
      <TaskCard task={done} onOpen={vi.fn()} onRetry={vi.fn()} onUrlError={onUrlError} />
    );
    const video = container.querySelector("video") as HTMLVideoElement;
    fireEvent.error(video);
    fireEvent.error(video);
    fireEvent.error(video);
    expect(onUrlError).toHaveBeenCalledTimes(1);
  });
});

describe("TaskCard AI 标识徽标（LABEL-TOGGLE-UI-0001，按任务状态两态）", () => {
  const doneTask = (applyVisibleLabel?: boolean): TrackedTask => ({
    taskId: "d1",
    topic: "T",
    status: "done",
    progress: 100,
    statusLabel: "已完成",
    playbackUrl: "https://example.test/v.mp4",
    applyVisibleLabel
  });

  it("带标识(applyVisibleLabel=true) + done → 显示「已含 AI 生成标识」徽标", () => {
    render(<TaskCard task={doneTask(true)} onOpen={vi.fn()} onRetry={vi.fn()} onUrlError={vi.fn()} />);
    expect(screen.getByText(copy.label.productNotice)).toBeInTheDocument();
  });

  it("不带标识(applyVisibleLabel=false) + done → 不显示徽标", () => {
    render(<TaskCard task={doneTask(false)} onOpen={vi.fn()} onRetry={vi.fn()} onUrlError={vi.fn()} />);
    expect(screen.queryByText(copy.label.productNotice)).not.toBeInTheDocument();
  });
});

describe("TaskCard photo error friendly (IMAGE-ERROR-FRIENDLY)", () => {
  it("shows friendly copy for a failed photo task — never the raw error_message", () => {
    const task: TrackedTask = {
      ...failed,
      mode: "photo",
      errorCode: "IMAGE_MODERATION_BLOCKED",
      error: 'Error code: 400 - {"error":{"code":"moderation_blocked"}}'
    };
    render(<TaskCard task={task} onOpen={vi.fn()} onRetry={vi.fn()} onUrlError={vi.fn()} />);
    expect(screen.getByText(copy.errors.imageModeration)).toBeInTheDocument();
    expect(screen.queryByText(/Error code: 400/)).toBeNull();
    expect(screen.queryByText(/moderation_blocked/)).toBeNull();
  });

  it("uses a generic friendly line for a failed photo with unknown error_code", () => {
    const task: TrackedTask = { ...failed, mode: "photo", errorCode: null, error: "Error code: 500 raw" };
    render(<TaskCard task={task} onOpen={vi.fn()} onRetry={vi.fn()} onUrlError={vi.fn()} />);
    expect(screen.getByText(copy.errors.imageGeneric)).toBeInTheDocument();
    expect(screen.queryByText(/Error code/)).toBeNull();
  });

  it("does NOT change video error display (avatar/i2v keep the original message)", () => {
    const task: TrackedTask = { ...failed, error: "积分不足，无法生成" };
    render(<TaskCard task={task} onOpen={vi.fn()} onRetry={vi.fn()} onUrlError={vi.fn()} />);
    expect(screen.getByText("积分不足，无法生成")).toBeInTheDocument();
  });
});
