import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the videos API so no real fetch happens.
vi.mock("@/lib/api/videos", () => ({
  listVideos: vi.fn().mockResolvedValue([]),
  createVideo: vi.fn(),
  getVideo: vi.fn().mockResolvedValue({}),
  streamVideoEvents: vi.fn().mockResolvedValue(undefined)
}));

// Controllable auth session.
let mockSession: { token: string } | null = null;
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: mockSession, ready: true, login: vi.fn(), logout: vi.fn() })
}));

import { listVideos } from "@/lib/api/videos";

import { VideoTasksProvider } from "./tasks-context";

beforeEach(() => {
  mockSession = null;
});
afterEach(() => {
  vi.clearAllMocks();
});

describe("VideoTasksProvider hydrate gating", () => {
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
