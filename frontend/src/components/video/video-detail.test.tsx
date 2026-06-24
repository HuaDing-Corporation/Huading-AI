import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

// Mock next/navigation
vi.mock("next/navigation", () => ({
  useRouter: () => ({ back: vi.fn(), push: vi.fn(), replace: vi.fn() })
}));

// Mock auth context
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: { token: "t" }, ready: true, login: vi.fn(), logout: vi.fn() })
}));

// Mock useVideo hook
vi.mock("@/lib/api/hooks", () => ({
  useVideo: vi.fn()
}));

import { ApiError } from "@/lib/api/client";
import { useVideo } from "@/lib/api/hooks";
import { VideoDetail } from "./video-detail";
import { copy } from "@/lib/copy";
import type { Mock } from "vitest";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("VideoDetail", () => {
  it("renders loading state while isLoading", () => {
    (useVideo as Mock).mockReturnValue({ data: undefined, error: null, isLoading: true });
    render(<VideoDetail id="v1" />, { wrapper });
    expect(screen.getByText("加载中…")).toBeInTheDocument();
  });

  it("nf-1: renders dedicated not-found empty state for 404 ApiError", () => {
    const err = new ApiError("Not Found", "not_found", 404);
    (useVideo as Mock).mockReturnValue({ data: undefined, error: err, isLoading: false });
    render(<VideoDetail id="missing" />, { wrapper });
    expect(screen.getByText(copy.detail.notFound)).toBeInTheDocument();
    expect(screen.getByText(copy.detail.back)).toBeInTheDocument();
  });

  it("nf-1: renders not-found state for error.code === 'not_found' regardless of status", () => {
    const err = new ApiError("Cross-tenant", "not_found", 403);
    (useVideo as Mock).mockReturnValue({ data: undefined, error: err, isLoading: false });
    render(<VideoDetail id="cross-tenant" />, { wrapper });
    expect(screen.getByText(copy.detail.notFound)).toBeInTheDocument();
  });

  it("renders video detail when data is present", () => {
    (useVideo as Mock).mockReturnValue({
      data: {
        id: "v1",
        status: "done",
        progress: 100,
        topic: "测试视频",
        script: "今天的主题是测试",
        voice_id: "v-zhixing",
        aspect_ratio: "9:16",
        subtitle_enabled: true,
        playback_url: "https://mock.local/v.mp4",
        download_url: "https://mock.local/v.mp4?dl=1",
        thumbnail_url: null,
        duration_ms: 30000,
        created_at: "2026-06-18T00:00:00Z"
      },
      error: null,
      isLoading: false
    });
    render(<VideoDetail id="v1" />, { wrapper });
    expect(screen.getByText("测试视频")).toBeInTheDocument();
    expect(screen.getByText("今天的主题是测试")).toBeInTheDocument();
    expect(screen.getByText(copy.detail.download)).toBeInTheDocument();
  });

  it("renders an <img> result for a photo task (mode=photo) with a download link", () => {
    (useVideo as Mock).mockReturnValue({
      data: {
        id: "p1",
        status: "done",
        progress: 100,
        mode: "photo",
        topic: "白色大理石上的香水瓶",
        script: null,
        voice_id: null,
        aspect_ratio: null,
        subtitle_enabled: null,
        playback_url: "https://mock.local/p.png",
        download_url: "https://mock.local/p.png?dl=1",
        thumbnail_url: null,
        created_at: "2026-06-23T00:00:00Z"
      },
      error: null,
      isLoading: false
    });
    render(<VideoDetail id="p1" />, { wrapper });
    expect(screen.getByRole("img")).toHaveAttribute("src", "https://mock.local/p.png");
    expect(screen.getByText(copy.detail.downloadImage)).toBeInTheDocument();
  });

  it("P2-3: renders without crashing when backend nullable fields are null", () => {
    (useVideo as Mock).mockReturnValue({
      data: {
        id: "v2",
        status: "running",
        progress: 40,
        topic: null,
        script: null,
        voice_id: null,
        aspect_ratio: null,
        subtitle_enabled: null,
        thumbnail_url: null,
        created_at: "2026-06-18T00:00:00Z"
      },
      error: null,
      isLoading: false
    });
    render(<VideoDetail id="v2" />, { wrapper });
    // null topic falls back to a placeholder; no crash, no subtitle block.
    expect(screen.getByText("未命名视频")).toBeInTheDocument();
  });
});
