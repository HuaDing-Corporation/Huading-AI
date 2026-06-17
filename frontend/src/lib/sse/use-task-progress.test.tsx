import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";

vi.mock("@/lib/api/videos", () => ({
  streamVideoEvents: vi.fn(),
  getVideo: vi.fn()
}));

import { getVideo, streamVideoEvents } from "@/lib/api/videos";
import { useTaskProgress } from "./use-task-progress";

afterEach(() => vi.clearAllMocks());

describe("useTaskProgress", () => {
  it("reflects SSE progress events", async () => {
    (streamVideoEvents as Mock).mockImplementation(
      async (_id: string, onMessage: (e: unknown) => void) => {
        onMessage({ status: "PROGRESS", progress: 0.5 });
      }
    );
    (getVideo as Mock).mockResolvedValue({
      id: "v1",
      title: "T",
      prompt: "",
      mode: "x",
      status: "running",
      progress: 50,
      created_at: ""
    });
    const { result } = renderHook(() => useTaskProgress("v1"));
    await waitFor(() => expect(result.current.status).toBe("running"));
    expect(result.current.progress).toBe(50);
  });
});
