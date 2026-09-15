import { act, cleanup, render } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  createVideo: vi.fn(), listVideos: vi.fn(), getVideo: vi.fn(),
  streamVideoEvents: vi.fn(), estimateVideo: vi.fn()
}));
const auth = vi.hoisted(() => ({ session: { token: "test-session" }, ready: true }));
vi.mock("@/lib/api/videos", () => api);
vi.mock("@/lib/auth/auth-context", () => ({ useAuth: () => auth }));
vi.mock("@/lib/sse/constants", () => ({ STALL_MS: 100, HARD_CAP_MS: 300, WATCHDOG_TICK_MS: 10, POLL_MS: 50 }));

import { VideoTasksProvider, useVideoTasks } from "./tasks-context";
import type { VideoEvent } from "@/lib/api/types";
let current: ReturnType<typeof useVideoTasks>;
function Harness() {
  const value = useVideoTasks();
  useEffect(() => { current = value; }, [value]);
  return null;
}

beforeEach(() => {
  vi.useFakeTimers();
  api.createVideo.mockResolvedValue({ pricing_contract: "legacy_estimate", id: "new-avatar", status: "queued" });
  api.listVideos.mockResolvedValue([]);
  api.getVideo.mockRejectedValue(new Error("offline"));
  api.streamVideoEvents.mockImplementation(() => new Promise(() => {}));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); });

async function start() {
  render(<VideoTasksProvider><Harness /></VideoTasksProvider>);
  await act(async () => {
    await current.createAndTrack({ topic: "口播", voice_id: "voice", avatar_asset_id: "photo" }, "口播");
  });
}

describe("HeyGen 结果未知不可变成本地失败→新付费任务", () => {
  it("已确认失败也不能自动复用同意创建新付费任务，须回工作台重新确认", async () => {
    let emit!: (event: VideoEvent) => void;
    api.streamVideoEvents.mockImplementation((_id, callback) => { emit = callback; return new Promise(() => {}); });
    await start();
    await act(async () => { emit({ status: "failed", progress: 40 }); });
    api.getVideo.mockResolvedValue({ id: "new-avatar", mode: "avatar_talk", avatar_provider: "heygen", status: "failed", progress: 40, topic: "口播", created_at: "" });
    api.estimateVideo.mockResolvedValue({ pricing_contract: "legacy_estimate", estimated_credits: 12, unit: "credits" });
    await act(async () => { await expect(current.retryTask("new-avatar")).rejects.toThrow(/重新确认/); });
    expect(api.estimateVideo).not.toHaveBeenCalled();
    expect(api.createVideo).toHaveBeenCalledTimes(1);
  });
  it("连接恢复收到终态后移除本地未知提示", async () => {
    let emit!: (event: VideoEvent) => void;
    api.streamVideoEvents.mockImplementation((_id, callback) => { emit = callback; return new Promise(() => {}); });
    await start();
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    expect(current.tasks[0].connectionUncertain).toBe(true);
    await act(async () => { emit({ status: "done", progress: 100 }); });
    expect(current.tasks[0].status).toBe("done");
    expect(current.tasks[0].connectionUncertain).toBe(false);
  });
  it("即使SSE曾报failed，重试前权威GET仍running就不新建", async () => {
    let emit!: (event: VideoEvent) => void;
    api.streamVideoEvents.mockImplementation((_id, callback) => { emit = callback; return new Promise(() => {}); });
    await start();
    await act(async () => { emit({ status: "failed", progress: 40 }); });
    api.getVideo.mockResolvedValue({ id: "new-avatar", mode: "avatar_talk", avatar_provider: "heygen", status: "running", progress: 40, topic: "口播", created_at: "" });
    await act(async () => { await expect(current.retryTask("new-avatar")).rejects.toThrow(); });
    expect(api.getVideo).toHaveBeenCalledWith("new-avatar");
    expect(api.estimateVideo).not.toHaveBeenCalled();
    expect(api.createVideo).toHaveBeenCalledTimes(1);
  });
  it.each(["watchdog", "poll"])("%s 断联仍保留任务，手动retry也不能发第二次POST", async (path) => {
    if (path === "poll") api.streamVideoEvents.mockRejectedValue(new Error("stream lost"));
    await start();
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    expect(current.tasks[0].status).toBe("running");
    expect(current.tasks[0].statusLabel).toMatch(/结果待确认/);
    await expect(current.retryTask("new-avatar")).rejects.toThrow();
    expect(api.createVideo).toHaveBeenCalledTimes(1);
    expect(api.estimateVideo).not.toHaveBeenCalled();
  });

  it("重新挂载后GET/list的HeyGen快照仍阻止watchdog误杀", async () => {
    api.listVideos.mockResolvedValue([{
      id: "restored", status: "running", progress: 40, topic: "旧会话",
      mode: "avatar_talk", avatar_provider: "heygen", avatar_model: "avatar_iv", created_at: ""
    }]);
    render(<VideoTasksProvider><Harness /></VideoTasksProvider>);
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    expect(current.tasks[0].status).toBe("running");
    await expect(current.retryTask("restored")).rejects.toThrow();
    expect(api.createVideo).not.toHaveBeenCalled();
  });
});
