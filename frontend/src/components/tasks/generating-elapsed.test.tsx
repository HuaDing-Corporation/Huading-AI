import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TrackedTask } from "@/lib/sse/progress-mapping";

import { GeneratingElapsed } from "./generating-elapsed";

// GEN-HEARTBEAT-UI-0001 · 诚实等待反馈承重（冻结 §四）。
// 钉两件事：① 文案里的时间**真的随时间推进**（不是恒显示「已 0 秒」）；
//          ② 它**不渲染任何百分比**——心跳期间百分比是冻结的，这条线只报计时，不报进度。
// 期望值手写（不 import 被测代码的常量/格式化器），否则格式化器一改期望值跟着变，等于没有网。

const T0 = 1_756_000_000_000; // 固定基准，避免真实时钟漂移

function task(over: Partial<TrackedTask> = {}): TrackedTask {
  return {
    taskId: "t1",
    topic: "T",
    status: "running",
    progress: 30,
    statusLabel: "生成中 30%",
    startedAt: T0,
    heartbeatAt: T0 + 1_000,
    ...over
  };
}

describe("GeneratingElapsed · 诚实计时（不是进度）", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(T0 + 200_000); // 任务已开始 200s = 3 分 20 秒
  });
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  // 🔴 门4：文案随时间推进（真实计时），且全程没有百分比。
  // 变异：把 GeneratingElapsed 的 setInterval 去掉（计时冻住）→ 本条必红（60s 后文案还是 3 分 20 秒）。
  it("计时随时间推进：3 分 20 秒 → 推进 60s → 4 分 20 秒；且不渲染任何百分比", () => {
    const { container } = render(<GeneratingElapsed task={task()} />);
    expect(screen.getByText("仍在生成（已 3 分 20 秒）")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByText("仍在生成（已 4 分 20 秒）")).toBeInTheDocument();
    expect(screen.queryByText("仍在生成（已 3 分 20 秒）")).toBeNull(); // 确实动了，不是并排多渲染了一行

    // 诚实红线：这条线只报时间，绝不报进度——出现 "%" 就是把等待伪装成推进。
    expect(container.textContent).not.toContain("%");
    expect(container.textContent).not.toContain("30");
  });

  // 门控：没有心跳 = 没有"还活着"的证据 → 一个字都不说（不能替后端担保）。
  it("从未收到心跳 → 不渲染（heartbeatAt 缺省）", () => {
    const { container } = render(<GeneratingElapsed task={task({ heartbeatAt: undefined })} />);
    expect(container.textContent).toBe("");
  });

  it("终态（done/failed）→ 不渲染，即便收到过心跳", () => {
    const done = render(<GeneratingElapsed task={task({ status: "done" })} />);
    expect(done.container.textContent).toBe("");
    const failed = render(<GeneratingElapsed task={task({ status: "failed" })} />);
    expect(failed.container.textContent).toBe("");
  });

  // 计时基准是**任务开始**，不是收到第一个心跳（冻结 §四）——否则等了很久才来首个心跳的任务
  // 会显示「已 0 秒」，那是低报等待时间。
  it("计时基准 = startedAt（任务开始），不是首个心跳时刻", () => {
    // 任务 200s 前开始，但第一个心跳是"刚刚"才到的。
    render(<GeneratingElapsed task={task({ heartbeatAt: T0 + 199_000 })} />);
    expect(screen.getByText("仍在生成（已 3 分 20 秒）")).toBeInTheDocument();
    expect(screen.queryByText("仍在生成（已 1 秒）")).toBeNull();
  });
});
