import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

// Mock the videos API so no real fetch happens.
vi.mock("@/lib/api/videos", () => ({
  listVideos: vi.fn().mockResolvedValue([]),
  createVideo: vi.fn().mockResolvedValue({ id: "t1", status: "queued" }),
  getVideo: vi.fn().mockResolvedValue({}),
  streamVideoEvents: vi.fn().mockResolvedValue(undefined)
}));

// Controllable auth session.
let mockSession: { token: string } | null = null;
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: mockSession, ready: true, login: vi.fn(), logout: vi.fn() })
}));

// Tiny windows so the watchdog trips inside the fake-timer advance.
vi.mock("@/lib/sse/constants", async (orig) => ({
  ...(await orig<typeof import("@/lib/sse/constants")>()),
  STALL_MS: 50,
  HARD_CAP_MS: 10_000,
  POLL_MS: 10_000
}));

import { listVideos, streamVideoEvents } from "@/lib/api/videos";

import { VideoTasksProvider, useVideoTasks } from "./tasks-context";

describe("VideoTasksProvider hydrate gating", () => {
  beforeEach(() => {
    mockSession = null;
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("does not fetch the list when there is no session", async () => {
    render(
      <VideoTasksProvider>
        <div>child</div>
      </VideoTasksProvider>
    );
    await new Promise((r) => setTimeout(r, 0)); // let mount effects flush
    expect(listVideos).not.toHaveBeenCalled();
  });

  it("fetches the list exactly once when a session is present", async () => {
    mockSession = { token: "t" };
    render(
      <VideoTasksProvider>
        <div>child</div>
      </VideoTasksProvider>
    );
    await waitFor(() => expect(listVideos).toHaveBeenCalledTimes(1));
  });
});

function Harness() {
  const { tasks, createAndTrack } = useVideoTasks();
  return (
    <div>
      <button onClick={() => void createAndTrack({ topic: "x", voice_id: "v", avatar_asset_id: "a" }, "x")}>go</button>
      <span data-testid="status">{tasks[0]?.status ?? "-"}</span>
    </div>
  );
}

describe("tasks-context client self-timeout", () => {
  beforeEach(() => {
    mockSession = { token: "t" };
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("forces failed when SSE only sends running and never a terminal frame", async () => {
    // SSE stays open emitting one running frame, never resolves/terminal.
    (streamVideoEvents as Mock).mockImplementation(async (_id, onMessage) => {
      onMessage({ status: "running", progress: 10, step: "avatar" });
      await new Promise(() => {}); // never resolves (stuck task)
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
      await vi.advanceTimersByTimeAsync(200); // > STALL_MS(50)
    });
    // Watchdog has already forced the terminal state synchronously during the
    // timer advance; assert directly (waitFor would deadlock under fake timers).
    expect(getByTestId("status").textContent).toBe("failed");
  });
});
