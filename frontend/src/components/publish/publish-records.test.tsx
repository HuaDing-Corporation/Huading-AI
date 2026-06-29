import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

const recordsMock = vi.hoisted(() => ({ data: [] as Array<Record<string, unknown>>, isLoading: false, isError: false, refetch: vi.fn() }));
const deleteMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));

vi.mock("@/lib/api/hooks", () => ({
  usePublishRecords: () => ({ data: recordsMock.data, isLoading: recordsMock.isLoading, isError: recordsMock.isError, refetch: recordsMock.refetch }),
  useDeletePublishRecord: () => ({ mutateAsync: deleteMock.mutateAsync, isPending: deleteMock.isPending })
}));

import { PublishRecords } from "./publish-records";

const rec = (id: string, title: string, platform: string, status: string) => ({
  id, title, platform, status, source_kind: "video", source_task_id: "v1", text: "", topics: [], publish_url: "x", created_at: ""
});

beforeEach(() => {
  recordsMock.isLoading = false;
  recordsMock.isError = false;
  recordsMock.data = [];
  deleteMock.isPending = false;
  deleteMock.mutateAsync.mockResolvedValue({ deleted: true });
});
afterEach(() => vi.clearAllMocks());

describe("PublishRecords (发布记录)", () => {
  it("渲染记录：平台名 + 状态徽章(草稿/已发布)", () => {
    recordsMock.data = [rec("p1", "抖音稿", "douyin", "draft"), rec("p2", "B站稿", "bilibili", "published")];
    render(<PublishRecords />);
    expect(screen.getByText("抖音稿")).toBeInTheDocument();
    expect(screen.getByText("B站稿")).toBeInTheDocument();
    expect(screen.getByText(copy.publish.statusDraft)).toBeInTheDocument();
    expect(screen.getByText(copy.publish.statusPublished)).toBeInTheDocument();
  });

  it("空态：提示暂无记录", () => {
    render(<PublishRecords />);
    expect(screen.getByText(copy.publish.recordsEmpty)).toBeInTheDocument();
  });

  it("删除：点删除→确认→调 deletePublishRecord(id)", async () => {
    recordsMock.data = [rec("p1", "抖音稿", "douyin", "draft")];
    render(<PublishRecords />);
    fireEvent.click(screen.getByRole("button", { name: `${copy.publish.deleteRecord} 抖音稿` }));
    expect(screen.getByText(copy.publish.deleteConfirmTitle)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: copy.publish.deleteConfirmBtn }));
    await waitFor(() => expect(deleteMock.mutateAsync).toHaveBeenCalledWith("p1"));
  });

  it("删除失败：弹窗内显示友好错误且弹窗仍开", async () => {
    deleteMock.mutateAsync.mockRejectedValue(new Error("boom"));
    recordsMock.data = [rec("p1", "抖音稿", "douyin", "draft")];
    render(<PublishRecords />);
    fireEvent.click(screen.getByRole("button", { name: `${copy.publish.deleteRecord} 抖音稿` }));
    fireEvent.click(screen.getByRole("button", { name: copy.publish.deleteConfirmBtn }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(copy.publish.deleteConfirmTitle)).toBeInTheDocument(); // 弹窗仍开
  });

  it("加载中 / 加载失败+重试", () => {
    recordsMock.isLoading = true;
    const { rerender } = render(<PublishRecords />);
    expect(screen.getByText(copy.publish.recordsLoading)).toBeInTheDocument();
    recordsMock.isLoading = false;
    recordsMock.isError = true;
    rerender(<PublishRecords />);
    expect(screen.getByText(copy.publish.recordsError)).toBeInTheDocument();
    fireEvent.click(screen.getByText(copy.cover.retry));
    expect(recordsMock.refetch).toHaveBeenCalled();
  });
});
