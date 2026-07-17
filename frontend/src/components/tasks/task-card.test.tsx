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

describe("TaskCard cancelled 状态（ECOM-HISTORY-CANCELLED-FIX-0001 · 根治电商历史 #130）", () => {
  // 后端 VideoTask.status 含 cancelled（批量生产 cancel 退分产生）；旧 thumbIcon 只覆盖 4 档 →
  // thumbIcon["cancelled"]=undefined → <Icon/> → React #130，整个电商历史白屏。承重（先红后绿）。
  it("cancelled 历史项 → 正常渲染「已取消」状态徽标，不 #130 白屏", () => {
    const cancelled: TrackedTask = {
      taskId: "c1",
      topic: "退款任务",
      status: "cancelled",
      progress: 100,
      statusLabel: "已取消"
    };
    render(<TaskCard task={cancelled} onOpen={vi.fn()} onRetry={vi.fn()} onUrlError={vi.fn()} />);
    expect(screen.getByText("退款任务")).toBeInTheDocument();
    expect(screen.getByText("已取消")).toBeInTheDocument(); // StatusBadge 覆盖 cancelled
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

  // VIDEO-ERR-MAP-UI：视频失败也走友好中文映射（照抄图片线），不再露裸 error_message / 技术串。
  it("视频失败·已知码 VIDEO_TIMEOUT → 友好中文，且不露裸 error", () => {
    const task: TrackedTask = { ...failed, errorCode: "VIDEO_TIMEOUT", error: "Error code: 504 - upstream timeout" };
    render(<TaskCard task={task} onOpen={vi.fn()} onRetry={vi.fn()} onUrlError={vi.fn()} />);
    expect(screen.getByText(copy.errors.videoTimeout)).toBeInTheDocument();
    expect(screen.queryByText(/Error code: 504/)).toBeNull();
  });

  it("视频失败·未知/缺失码 → 通用视频兜底文案，绝不回落裸 error_message", () => {
    const task: TrackedTask = { ...failed, errorCode: null, error: "RuntimeError: something exploded" };
    render(<TaskCard task={task} onOpen={vi.fn()} onRetry={vi.fn()} onUrlError={vi.fn()} />);
    expect(screen.getByText(copy.errors.videoGeneric)).toBeInTheDocument();
    expect(screen.queryByText(/RuntimeError/)).toBeNull();
  });
});

// ── HISTORY-VIDEO-DIALOG-UI-0001 · 🔴 补网（这批必须在改造**之前**就绿）──
// 视频卡片的 **封面 poster** 与 **「查看详情」跳转** 此前零测试。本包要动它们，而「零回归证据」的前提是先有网：
// 没有网，改完说「没坏」就只是一句话。顺序纪律：补网 → 绿 → 再解耦/升级。
const doneVideo: TrackedTask = {
  taskId: "v-1",
  topic: "保温杯带货",
  status: "done",
  progress: 100,
  statusLabel: "已完成",
  playbackUrl: "https://cdn/v-1.mp4",
  thumbnailUrl: "https://cdn/v-1.jpg"
};

describe("TaskCard 封面 poster（补网 · 改造前基线）", () => {
  it("有 thumbnailUrl → <video poster> 用它作封面", () => {
    render(<TaskCard task={doneVideo} onOpen={vi.fn()} onRetry={vi.fn()} onUrlError={vi.fn()} />);
    expect(document.querySelector("video")).toHaveAttribute("poster", "https://cdn/v-1.jpg");
  });

  it("无 thumbnailUrl → 不设 poster（不冒充空封面）", () => {
    render(
      <TaskCard task={{ ...doneVideo, thumbnailUrl: null }} onOpen={vi.fn()} onRetry={vi.fn()} onUrlError={vi.fn()} />
    );
    expect(document.querySelector("video")).not.toHaveAttribute("poster");
  });

  it("photo 模式走 <img>（无 video、无 poster）——封面只属于视频分支", () => {
    render(
      <TaskCard task={{ ...doneVideo, mode: "photo" }} onOpen={vi.fn()} onRetry={vi.fn()} onUrlError={vi.fn()} />
    );
    expect(document.querySelector("video")).toBeNull();
    expect(document.querySelector("img")).toHaveAttribute("src", "https://cdn/v-1.mp4");
  });
});

describe("TaskCard「查看详情」跳转（补网 · 改造前基线）", () => {
  it("done + 有播放地址 → 点「查看详情」以 taskId 恰调一次 onOpen", () => {
    const onOpen = vi.fn();
    render(<TaskCard task={doneVideo} onOpen={onOpen} onRetry={vi.fn()} onUrlError={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith("v-1");
  });

  // done 但**尚无 playbackUrl**（仍在对账）是另一个渲染分支（task-card.tsx:199-209）——改造时最容易漏掉的一支。
  it("done + 尚无播放地址（对账中）→ 仍有「查看详情」，且以 taskId 调 onOpen", () => {
    const onOpen = vi.fn();
    render(
      <TaskCard task={{ ...doneVideo, playbackUrl: null }} onOpen={onOpen} onRetry={vi.fn()} onUrlError={vi.fn()} />
    );
    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));
    expect(onOpen).toHaveBeenCalledWith("v-1");
  });

  it("running → 无「查看详情」（只有进度条）", () => {
    const onOpen = vi.fn();
    render(
      <TaskCard
        task={{ ...doneVideo, status: "running", playbackUrl: null, progress: 40 }}
        onOpen={onOpen}
        onRetry={vi.fn()}
        onUrlError={vi.fn()}
      />
    );
    expect(screen.queryByRole("button", { name: copy.tasks.open })).not.toBeInTheDocument();
    expect(onOpen).not.toHaveBeenCalled();
  });
});
