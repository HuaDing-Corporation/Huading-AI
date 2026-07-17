import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const historyMock = vi.hoisted(() => ({ fn: vi.fn() }));
const kindMock = vi.hoisted(() => ({ fn: vi.fn() }));
const histImgMock = vi.hoisted(() => ({ fn: vi.fn() })); // HISTORY-IMAGE-TAB-UI-0001：捕获 useHistoryImages(category)
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
  useClearCopyDrafts: () => clearDraftsMock,
  // HISTORY-IMAGE-TAB-UI-0001：图片 tab 换归一 API → PhotoHistory 挂 HistoryGrid，需这两个 hook（FIX1 摘删除后不再需要图片删除钩子）。
  useHistoryImages: (category?: string) => {
    histImgMock.fn(category);
    return { data: { pages: [{ items: [], total: 0 }] }, isLoading: false, isError: false, hasNextPage: false, isFetchingNextPage: false, fetchNextPage: vi.fn(), refetch: vi.fn() };
  },
  useHistoryImageSet: () => ({ data: undefined, isLoading: false, isError: false, refetch: vi.fn() })
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

  // HISTORY-IMAGE-TAB-UI-0001：图片 tab 换归一 API + 6 分类（全部图片=不传 category）。
  it("图片 tab：默认「全部图片」(category undefined)，点分类 chip → 传对应 category（6 分类含封面）", () => {
    render(<PhotoHistory />);
    expect(histImgMock.fn).toHaveBeenLastCalledWith(undefined); // 默认全部图片 = 不传 category
    fireEvent.click(screen.getByRole("button", { name: "封面" }));
    expect(histImgMock.fn).toHaveBeenLastCalledWith("cover");
    fireEvent.click(screen.getByRole("button", { name: "电商·白底图" }));
    expect(histImgMock.fn).toHaveBeenLastCalledWith("ecom_white");
    fireEvent.click(screen.getByRole("button", { name: "全部图片" }));
    expect(histImgMock.fn).toHaveBeenLastCalledWith(undefined);
  });

  // 🔴 COPY-DRAFT-DELETE-COPY-FIX-0001：这两条原本**在给谎言站岗** —— 它们断言用户**必须**看到
  // 「（可恢复）」，测试名还叫「(可恢复文案)」。而 BE 那边：delete_draft/clear_drafts 只置 deleted_at
  // （services/copy.py:378 / :393），list_drafts + get_draft 都过滤 deleted_at（:354 / :368）
  // → 列表消失 + 详情 404，且**全仓零恢复入口**（restore/undelete/deleted_at=None 一处都搜不到）。
  // 「可恢复」是**把 BE 的运维保险当成对用户的承诺**。
  //
  // 注意这两条是**写死字面量**的（不是引用 copy key）→ `grep deleteConfirmSoft` **扫不到它们**。
  // 谎言不靠 key 名传播，靠句子传播 —— 所以下面的反断言同样钉字面量。
  it("文案 history：删除确认门 → 恰调一次 DELETE /copy/drafts/{id}；文案只讲用户可观察的后果", async () => {
    render(<CopyDraftList />);
    expect(screen.getByText("草稿文案A")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("删除"));
    expect(deleteDraftMock.mutateAsync).not.toHaveBeenCalled(); // 确认门：不直接删

    expect(screen.getByText("将从历史移除，无法撤销。")).toBeInTheDocument();
    expect(screen.queryByText("将从历史移除（可恢复）。")).not.toBeInTheDocument(); // 不许把谎加回来
    expect(screen.queryByText("将永久删除，不可恢复。")).not.toBeInTheDocument(); // 也不许反方向的谎（数据其实都在）

    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(deleteDraftMock.mutateAsync).toHaveBeenCalledWith("d-1"));
    expect(deleteDraftMock.mutateAsync).toHaveBeenCalledTimes(1);
  });

  it("文案清空：确认门 → 恰调一次 DELETE /copy/drafts；文案同样不说「可恢复」", async () => {
    render(<CopyDraftList />);
    fireEvent.click(screen.getByRole("button", { name: /清空/ }));
    expect(clearDraftsMock.mutateAsync).not.toHaveBeenCalled();

    expect(screen.getByText("将清空此模块全部草稿，无法撤销。")).toBeInTheDocument();
    // clearConfirmSoft 是 deleteConfirmSoft 的孪生 —— 任务包只点了后者，只删一个 = 陷阱留一半。
    expect(screen.queryByText("将清空此模块全部草稿（可恢复）。")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "确认清空" }));
    await waitFor(() => expect(clearDraftsMock.mutateAsync).toHaveBeenCalledTimes(1));
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
