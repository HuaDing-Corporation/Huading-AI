import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

// GEN-TIMEOUT-1500-UI-0001 看门狗承重（**用真实 STALL_MS/HARD_CAP_MS**，故不 mock 常量——与 tasks-context.test.tsx
// 那份「mock 小值测机制」互补）：验证前端看门狗晚于 BE 权威超时（1500s），120s 无进度不再误杀，但兜底网仍在。
vi.mock("@/lib/api/videos", () => ({
  listVideos: vi.fn().mockResolvedValue([]),
  createVideo: vi.fn().mockResolvedValue({ id: "t1", status: "queued" }),
  getVideo: vi.fn().mockResolvedValue({}),
  streamVideoEvents: vi.fn().mockResolvedValue(undefined)
}));
let mockSession: { token: string } | null = { token: "t" };
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: mockSession, ready: true, login: vi.fn(), logout: vi.fn() })
}));

import { HARD_CAP_MS, STALL_MS } from "@/lib/sse/constants";
import { streamVideoEvents } from "@/lib/api/videos";

import { VideoTasksProvider, useVideoTasks } from "./tasks-context";

function Harness() {
  const { tasks, createAndTrack } = useVideoTasks();
  return (
    <div>
      <button onClick={() => void createAndTrack({ topic: "x", voice_id: "v", avatar_asset_id: "a" }, "x")}>go</button>
      <span data-testid="status">{tasks[0]?.status ?? "-"}</span>
    </div>
  );
}

describe("GEN-TIMEOUT-1500-UI-0001 · 看门狗晚于 BE 权威超时（1500s）", () => {
  it("🔴 阈值承重（变异锚点）：STALL_MS 与 HARD_CAP_MS 都 > BE 权威 1500s", () => {
    const BE_AUTHORITATIVE_MS = 1_500_000;
    expect(STALL_MS).toBeGreaterThan(BE_AUTHORITATIVE_MS);
    expect(HARD_CAP_MS).toBeGreaterThan(BE_AUTHORITATIVE_MS);
  });

  describe("行为承重（真实常量 + fake timers）", () => {
    beforeEach(() => {
      mockSession = { token: "t" };
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.runOnlyPendingTimers();
      vi.useRealTimers();
      vi.clearAllMocks();
    });

    it("🔴 120s 无进度但后端仍在跑 → 卡片不得 failed（图片生成必中场景，旧值会误杀）", async () => {
      // 图片生成：SSE 只来一帧 running 就没进度了，流不关（后端 worker 仍在等 APIMart）。
      (streamVideoEvents as Mock).mockImplementation(async (_id: string, onMessage: (e: unknown) => void) => {
        onMessage({ status: "running", progress: 30, step: null });
        await new Promise(() => {}); // 永不 resolve（后端仍在跑，SSE 未终结）
      });
      const { getByText, getByTestId } = render(
        <VideoTasksProvider>
          <Harness />
        </VideoTasksProvider>
      );
      await act(async () => {
        getByText("go").click();
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(125_000); // 125s：远超旧 STALL(120s)，但 < 新 STALL(1620s)
      });
      // 旧 120s 看门狗到此会误杀；现在不得 failed（仍是 running）。
      expect(getByTestId("status").textContent).not.toBe("failed");
      expect(getByTestId("status").textContent).toBe("running");
    });

    it("看门狗仍有效（别把网删了）：无任何更新超过新 STALL_MS → 仍标 failed", async () => {
      (streamVideoEvents as Mock).mockImplementation(async (_id: string, onMessage: (e: unknown) => void) => {
        onMessage({ status: "running", progress: 30, step: null });
        await new Promise(() => {}); // 永远卡死 = 后端失联
      });
      const { getByText, getByTestId } = render(
        <VideoTasksProvider>
          <Harness />
        </VideoTasksProvider>
      );
      await act(async () => {
        getByText("go").click();
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(STALL_MS + 3_000); // 超过新 STALL → 兜底网触发
      });
      expect(getByTestId("status").textContent).toBe("failed");
    });
  });
});
