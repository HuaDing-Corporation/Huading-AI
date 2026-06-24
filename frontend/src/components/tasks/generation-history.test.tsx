import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const historyMock = vi.hoisted(() => ({ fn: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/hooks", () => ({
  useVideoHistory: (mode: string) => {
    historyMock.fn(mode);
    return {
      data: {
        pages: [
          {
            items: [
              {
                id: `v-${mode}`,
                status: "done",
                progress: 100,
                topic: `${mode} 视频`,
                created_at: "",
                playback_url: `https://mock.local/${mode}.png`,
                download_url: `https://mock.local/${mode}.png?dl=1`
              }
            ],
            total: 1
          }
        ]
      },
      isLoading: false,
      isError: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      refetch: vi.fn()
    };
  }
}));

import { GenerationHistory, HistoryList } from "./generation-history";

afterEach(() => vi.clearAllMocks());

describe("GenerationHistory (历史生成 tabs)", () => {
  it("renders the 3 tabs and defaults to 数字人 (avatar_talk)", () => {
    render(<GenerationHistory />);
    expect(screen.getByRole("tab", { name: /数字人视频历史/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /电商视频历史/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /图片历史/ })).toBeInTheDocument();
    // default tab pulls the avatar_talk history
    expect(historyMock.fn).toHaveBeenCalledWith("avatar_talk");
    expect(screen.getByText("avatar_talk 视频")).toBeInTheDocument();
  });

  it("数字人 history pulls GET /videos?mode=avatar_talk", () => {
    render(<HistoryList mode="avatar_talk" />);
    expect(historyMock.fn).toHaveBeenCalledWith("avatar_talk");
    expect(screen.getByText("avatar_talk 视频")).toBeInTheDocument();
  });

  it("电商 history pulls GET /videos?mode=seedance_i2v", () => {
    render(<HistoryList mode="seedance_i2v" />);
    expect(historyMock.fn).toHaveBeenCalledWith("seedance_i2v");
    expect(screen.getByText("seedance_i2v 视频")).toBeInTheDocument();
  });

  it("照片 history pulls GET /videos?mode=photo and renders an <img> result", () => {
    render(<HistoryList mode="photo" />);
    expect(historyMock.fn).toHaveBeenCalledWith("photo");
    // photo results render as <img> (not <video>), alt = the topic.
    expect(screen.getByRole("img")).toHaveAttribute("alt", "photo 视频");
  });
});
