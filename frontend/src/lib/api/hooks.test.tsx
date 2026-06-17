import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";
import type { ReactNode } from "react";

vi.mock("@/lib/api/videos", () => ({
  listVideos: vi.fn(),
  getVideo: vi.fn(),
  createVideo: vi.fn()
}));
vi.mock("@/lib/api/uploads", () => ({ uploadImage: vi.fn() }));
vi.mock("@/lib/api/auth", () => ({ fetchMe: vi.fn() }));

let mockSession: { token: string } | null = { token: "t" };
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: mockSession, ready: true, login: vi.fn(), logout: vi.fn() })
}));

import { listVideos, createVideo } from "@/lib/api/videos";
import { useVideos, useCreateVideo } from "@/lib/api/hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => {
  vi.clearAllMocks();
  mockSession = { token: "t" };
});

describe("useVideos", () => {
  it("fetches the list when a session is present", async () => {
    (listVideos as Mock).mockResolvedValue([{ id: "v1" }]);
    const { result } = renderHook(() => useVideos(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([{ id: "v1" }]);
  });

  it("does not fetch when there is no session", async () => {
    mockSession = null;
    (listVideos as Mock).mockResolvedValue([]);
    renderHook(() => useVideos(), { wrapper });
    await new Promise((r) => setTimeout(r, 0));
    expect(listVideos).not.toHaveBeenCalled();
  });
});

describe("useCreateVideo", () => {
  it("creates a video via the mutation", async () => {
    (createVideo as Mock).mockResolvedValue({ task_id: "x", status: "queued" });
    const { result } = renderHook(() => useCreateVideo(), { wrapper });
    result.current.mutate({ topic: "hi" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(createVideo).toHaveBeenCalledWith({ topic: "hi" });
  });
});
