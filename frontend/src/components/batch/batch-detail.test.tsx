import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { BatchDetail } from "@/lib/api/types";

const batchMock = vi.hoisted(() => ({ data: undefined as BatchDetail | undefined, isLoading: false, isError: false, refetch: vi.fn() }));
const cancelMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const retryMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
vi.mock("@/lib/api/hooks", () => ({
  useBatch: () => batchMock,
  useCancelBatch: () => cancelMock,
  useRetryBatchTask: () => retryMock
}));

import { BatchDetail as BatchDetailView } from "./batch-detail";

const detail = (status: BatchDetail["batch"]["status"]): BatchDetail => ({
  batch: { id: "b1", kind: "ecom_table", status, total: 2, succeeded: 1, failed: 1, created_at: "", updated_at: "" },
  tasks: [
    { task_id: "b1-t0", row_index: 0, status: "done", video_url: "https://mock.local/v.mp4", error: null },
    { task_id: "b1-t1", row_index: 1, status: "failed", video_url: null, error: "生成失败（mock）" }
  ]
});

beforeEach(() => {
  batchMock.data = detail("partial_failed");
  cancelMock.mutateAsync.mockResolvedValue(detail("cancelled"));
  retryMock.mutateAsync.mockResolvedValue({ task_id: "b1-t1", status: "running" });
});
afterEach(() => vi.clearAllMocks());

describe("BatchDetail (批次详情)", () => {
  it("渲染子任务：done 出成片/下载，failed 内联错误 + 重试按钮（承重·失败行内联）", () => {
    render(<BatchDetailView batchId="b1" onBack={vi.fn()} />);
    expect(screen.getByText("生成失败（mock）")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: new RegExp(copy.batch.taskRetry) })).toBeInTheDocument();
    expect(screen.getByText(copy.detail.download)).toBeInTheDocument();
  });

  it("单条重试：点重试 → retryBatchTask(taskId)（承重）", async () => {
    render(<BatchDetailView batchId="b1" onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: new RegExp(copy.batch.taskRetry) }));
    await waitFor(() => expect(retryMock.mutateAsync).toHaveBeenCalledWith("b1-t1"));
  });

  it("running 批次显示取消按钮 → cancelBatch(id)", async () => {
    batchMock.data = detail("running");
    render(<BatchDetailView batchId="b1" onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: copy.batch.cancelBatch }));
    await waitFor(() => expect(cancelMock.mutateAsync).toHaveBeenCalledWith("b1"));
  });

  it("非 running 批次不显示取消", () => {
    render(<BatchDetailView batchId="b1" onBack={vi.fn()} />);
    expect(screen.queryByRole("button", { name: copy.batch.cancelBatch })).not.toBeInTheDocument();
  });

  it("返回：点返回 → onBack", () => {
    const onBack = vi.fn();
    render(<BatchDetailView batchId="b1" onBack={onBack} />);
    fireEvent.click(screen.getByRole("button", { name: new RegExp(copy.batch.backToList) }));
    expect(onBack).toHaveBeenCalled();
  });
});
