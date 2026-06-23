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
              { id: `v-${mode}`, status: "done", progress: 100, topic: `${mode} 视频`, created_at: "" }
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

import { GenerationHistory, HistoryList, PhotoComingSoon } from "./generation-history";

afterEach(() => vi.clearAllMocks());

describe("GenerationHistory (历史生成 tabs)", () => {
  it("renders the 3 tabs and defaults to 数字人 (avatar_talk)", () => {
    render(<GenerationHistory />);
    expect(screen.getByRole("tab", { name: /数字人视频历史/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /电商视频历史/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /照片历史/ })).toBeInTheDocument();
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

  it("照片历史 is a coming-soon placeholder (no history request)", () => {
    render(<PhotoComingSoon />);
    expect(screen.getByText("照片生成功能即将上线，敬请期待")).toBeInTheDocument();
    expect(historyMock.fn).not.toHaveBeenCalled();
  });
});
