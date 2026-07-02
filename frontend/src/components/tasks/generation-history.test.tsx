import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const historyMock = vi.hoisted(() => ({ fn: vi.fn() }));
const kindMock = vi.hoisted(() => ({ fn: vi.fn() }));
const draftsMock = vi.hoisted(() => ({ fn: vi.fn() }));
const deleteVideoMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false, variables: undefined as string | undefined }));
const clearVideosMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const deleteDraftMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false, variables: undefined as string | undefined }));
const clearDraftsMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/hooks", () => ({
  useVideoHistory: (mode: string, kind?: string) => {
    historyMock.fn(mode);
    kindMock.fn(kind);
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
  useDeleteVideo: () => deleteVideoMock,
  useClearVideos: () => clearVideosMock,
  useCopyDrafts: () => {
    draftsMock.fn();
    return {
      data: { pages: [{ items: [{ id: "d-1", source_text: "原", result_text: "草稿文案A", titles: ["标题X"], topics: ["#话题Y"], mode: "smart", created_at: "" }], total: 1 }] },
      isLoading: false,
      isError: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      refetch: vi.fn()
    };
  },
  useDeleteCopyDraft: () => deleteDraftMock,
  useClearCopyDrafts: () => clearDraftsMock
}));

import { CopyDraftList } from "./copy-draft-list";
import { GenerationHistory, HistoryList, PhotoHistory } from "./generation-history";

beforeEach(() => {
  deleteVideoMock.isPending = false;
  deleteVideoMock.variables = undefined;
  clearVideosMock.isPending = false;
  deleteDraftMock.isPending = false;
  deleteDraftMock.variables = undefined;
  clearDraftsMock.isPending = false;
  deleteVideoMock.mutateAsync.mockResolvedValue({ deleted: true });
  clearVideosMock.mutateAsync.mockResolvedValue({ deleted_count: 1 });
  deleteDraftMock.mutateAsync.mockResolvedValue({ deleted: true });
  clearDraftsMock.mutateAsync.mockResolvedValue({ deleted_count: 1 });
});
afterEach(() => vi.clearAllMocks());

describe("GenerationHistory (历史 tabs + 删除/清空/仅封面)", () => {
  it("renders the 4 tabs and defaults to 数字人 (avatar_talk)", () => {
    render(<GenerationHistory />);
    expect(screen.getByRole("tab", { name: /数字人视频历史/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /图片历史/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /文案历史/ })).toBeInTheDocument();
    expect(historyMock.fn).toHaveBeenCalledWith("avatar_talk");
  });

  it("照片 history pulls GET /videos?mode=photo and renders an <img>", () => {
    render(<HistoryList mode="photo" />);
    expect(historyMock.fn).toHaveBeenCalledWith("photo");
    expect(screen.getByRole("img")).toHaveAttribute("alt", "photo 视频");
  });

  it("删除单条：点 trash 先弹确认(拦截，不直接删) → 确认后才调 DELETE", async () => {
    render(<HistoryList mode="avatar_talk" />);
    // 点 trash → 仅弹确认，未直接删（确认弹窗拦截破坏性操作）
    fireEvent.click(screen.getByLabelText("删除"));
    expect(deleteVideoMock.mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByText("删除这条记录？")).toBeInTheDocument();
    expect(screen.getByText("将永久删除，不可恢复。")).toBeInTheDocument(); // 视频=硬删文案
    // 确认 → 调 DELETE /videos/{id}
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(deleteVideoMock.mutateAsync).toHaveBeenCalledWith("v-avatar_talk"));
  });

  it("清空：点清空 → 确认(硬删文案) → 调 DELETE /videos?mode=", async () => {
    render(<HistoryList mode="seedance_i2v" />);
    fireEvent.click(screen.getByRole("button", { name: /清空/ }));
    expect(clearVideosMock.mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByText("清空该历史？")).toBeInTheDocument();
    // 视频/图片=硬删文案
    expect(screen.getByText("将永久删除此模块全部记录，不可恢复。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认清空" }));
    await waitFor(() => expect(clearVideosMock.mutateAsync).toHaveBeenCalledWith("seedance_i2v"));
  });

  it("图片清空：确认文案明确「全部图片(含封面)、硬删、不受仅封面筛选影响」(P2)", () => {
    render(<HistoryList mode="photo" />);
    fireEvent.click(screen.getByRole("button", { name: /清空/ }));
    expect(
      screen.getByText("将清空全部图片（含封面），硬删不可恢复；不受当前「仅封面」筛选影响（始终删除全部图片）。")
    ).toBeInTheDocument();
  });

  it("防连点：删除 pending 时该条 trash 禁用", () => {
    deleteVideoMock.isPending = true;
    deleteVideoMock.variables = "v-avatar_talk";
    render(<HistoryList mode="avatar_talk" />);
    expect(screen.getByLabelText("删除")).toBeDisabled();
  });

  it("仅封面：PhotoHistory 默认全部(kind undefined)，点「仅封面」→ kind=cover", () => {
    render(<PhotoHistory />);
    expect(historyMock.fn).toHaveBeenCalledWith("photo");
    expect(kindMock.fn).toHaveBeenLastCalledWith(undefined);
    fireEvent.click(screen.getByRole("button", { name: "仅封面" }));
    expect(kindMock.fn).toHaveBeenLastCalledWith("cover");
    fireEvent.click(screen.getByRole("button", { name: "全部图片" }));
    expect(kindMock.fn).toHaveBeenLastCalledWith(undefined);
  });

  it("文案 history：删除单条软删确认(可恢复文案) → 调 DELETE /copy/drafts/{id}", async () => {
    render(<CopyDraftList />);
    expect(screen.getByText("草稿文案A")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("删除"));
    expect(deleteDraftMock.mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByText("将从历史移除（可恢复）。")).toBeInTheDocument(); // 文案=软删文案
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(deleteDraftMock.mutateAsync).toHaveBeenCalledWith("d-1"));
  });

  it("文案清空：点清空 → 软删确认文案(可恢复) → 调 DELETE /copy/drafts", async () => {
    render(<CopyDraftList />);
    fireEvent.click(screen.getByRole("button", { name: /清空/ }));
    expect(clearDraftsMock.mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByText("将清空此模块全部草稿（可恢复）。")).toBeInTheDocument(); // 文案=软删
    fireEvent.click(screen.getByRole("button", { name: "确认清空" }));
    await waitFor(() => expect(clearDraftsMock.mutateAsync).toHaveBeenCalled());
  });

  it("删除失败：弹窗内显示友好错误且弹窗仍开可重试(失败友好态)", async () => {
    deleteVideoMock.mutateAsync.mockRejectedValue(new Error("boom"));
    render(<HistoryList mode="avatar_talk" />);
    fireEvent.click(screen.getByLabelText("删除"));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    // 失败原因在弹窗内可见(不被模态遮罩盖)，且弹窗仍开可重试
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText("删除失败，请重试")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "确认删除" })).toBeInTheDocument();
  });

  it("弹窗防连点：清空 pending 时弹窗确认按钮显「处理中…」并禁用、取消亦禁用", () => {
    clearVideosMock.isPending = true;
    render(<HistoryList mode="avatar_talk" />);
    fireEvent.click(screen.getByRole("button", { name: /清空/ }));
    // submitting → 确认按钮文案变「处理中…」且禁用；取消也禁用(防连点)
    expect(screen.getByRole("button", { name: "处理中…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
  });
});
