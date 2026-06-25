import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const historyMock = vi.hoisted(() => ({ fn: vi.fn() }));
const draftsMock = vi.hoisted(() => ({ fn: vi.fn() }));

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
  },
  useCopyDrafts: () => {
    draftsMock.fn();
    return {
      data: {
        pages: [
          {
            items: [
              {
                id: "d-1",
                source_text: "原始文案",
                result_text: "草稿文案A",
                titles: ["标题X"],
                topics: ["#话题Y"],
                mode: "smart",
                created_at: ""
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

import { CopyDraftList } from "./copy-draft-list";
import { GenerationHistory, HistoryList } from "./generation-history";

afterEach(() => vi.clearAllMocks());

describe("GenerationHistory (历史生成 tabs)", () => {
  it("renders the 4 tabs and defaults to 数字人 (avatar_talk)", () => {
    render(<GenerationHistory />);
    expect(screen.getByRole("tab", { name: /数字人视频历史/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /电商视频历史/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /图片历史/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /文案历史/ })).toBeInTheDocument();
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

  it("文案 history pulls GET /copy/drafts and renders draft text + 标题/话题", () => {
    render(<CopyDraftList />);
    expect(draftsMock.fn).toHaveBeenCalled();
    expect(screen.getByText("草稿文案A")).toBeInTheDocument();
    expect(screen.getByText("标题X")).toBeInTheDocument();
    expect(screen.getByText("#话题Y")).toBeInTheDocument();
  });
});
