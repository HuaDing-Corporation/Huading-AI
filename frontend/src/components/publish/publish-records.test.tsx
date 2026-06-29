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

const rec = (id: string, taskId: string, platforms: { platform_id: string; status: string }[], sourceKind = "video") => ({
  id, source_kind: sourceKind, source_task_id: taskId, created_at: "", platforms
});

beforeEach(() => {
  recordsMock.isLoading = false;
  recordsMock.isError = false;
  recordsMock.data = [];
  deleteMock.isPending = false;
  deleteMock.mutateAsync.mockResolvedValue({ id: "p1", deleted_at: "" });
});
afterEach(() => vi.clearAllMocks());

describe("PublishRecords (发布记录 · 嵌套)", () => {
  it("渲染记录：产物来源 + 各平台状态 chip(草稿/已发布)", () => {
    recordsMock.data = [
      rec("p1", "v1", [{ platform_id: "douyin", status: "draft" }]),
      rec("p2", "v2", [{ platform_id: "bilibili", status: "published" }])
    ];
    render(<PublishRecords />);
    expect(screen.getByText("视频 · v1")).toBeInTheDocument();
    expect(screen.getByText(`${copy.publish.platformDouyin} · ${copy.publish.statusDraft}`)).toBeInTheDocument();
    expect(screen.getByText(`${copy.publish.platformBilibili} · ${copy.publish.statusPublished}`)).toBeInTheDocument();
  });

  it("空态：提示暂无记录", () => {
    render(<PublishRecords />);
    expect(screen.getByText(copy.publish.recordsEmpty)).toBeInTheDocument();
  });

  it("删除：点删除→确认→调 deletePublishRecord(id)", async () => {
    recordsMock.data = [rec("p1", "v1", [{ platform_id: "douyin", status: "draft" }])];
    render(<PublishRecords />);
    fireEvent.click(screen.getByRole("button", { name: `${copy.publish.deleteRecord} v1` }));
    expect(screen.getByText(copy.publish.deleteConfirmTitle)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: copy.publish.deleteConfirmBtn }));
    await waitFor(() => expect(deleteMock.mutateAsync).toHaveBeenCalledWith("p1"));
  });

  it("删除失败：弹窗内显示友好错误且弹窗仍开", async () => {
    deleteMock.mutateAsync.mockRejectedValue(new Error("boom"));
    recordsMock.data = [rec("p1", "v1", [{ platform_id: "douyin", status: "draft" }])];
    render(<PublishRecords />);
    fireEvent.click(screen.getByRole("button", { name: `${copy.publish.deleteRecord} v1` }));
    fireEvent.click(screen.getByRole("button", { name: copy.publish.deleteConfirmBtn }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(copy.publish.deleteConfirmTitle)).toBeInTheDocument();
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
