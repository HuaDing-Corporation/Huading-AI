import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const tasksMock = vi.hoisted(() => ({ tasks: [] as unknown[] }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/videos/tasks-context", () => ({
  useVideoTasks: () => ({ tasks: tasksMock.tasks, refreshTask: vi.fn(), retryTask: vi.fn() })
}));

import { TaskList } from "./task-list";

function doneTask(id: string, topic: string) {
  return { taskId: id, topic, status: "done", progress: 100, statusLabel: "已完成" };
}

afterEach(() => {
  tasksMock.tasks = [];
  vi.clearAllMocks();
});

describe("TaskList (生成任务 — latest 2 only)", () => {
  it("shows only the 2 most recent + a 历史生成 hint when there are more", () => {
    tasksMock.tasks = [doneTask("a", "任务A"), doneTask("b", "任务B"), doneTask("c", "任务C")];
    render(<TaskList />);
    expect(screen.getByText("任务A")).toBeInTheDocument();
    expect(screen.getByText("任务B")).toBeInTheDocument();
    expect(screen.queryByText("任务C")).not.toBeInTheDocument();
    expect(screen.getByText("更多任务见下方「历史生成」")).toBeInTheDocument();
  });

  it("shows no hint when there are 2 or fewer", () => {
    tasksMock.tasks = [doneTask("a", "任务A")];
    render(<TaskList />);
    expect(screen.getByText("任务A")).toBeInTheDocument();
    expect(screen.queryByText("更多任务见下方「历史生成」")).not.toBeInTheDocument();
  });
});
