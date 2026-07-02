import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { BatchDetail } from "@/lib/api/types";

const batchMock = vi.hoisted(() => ({ data: undefined as BatchDetail | undefined, isLoading: false, isError: false, refetch: vi.fn() }));
const cancelMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
vi.mock("@/lib/api/hooks", () => ({
  useBatch: () => batchMock,
  useCancelBatch: () => cancelMock
}));

import { BatchDetail as BatchDetailView } from "./batch-detail";

const detail = (status: BatchDetail["batch"]["status"]): BatchDetail => ({
  batch: { id: "b1", kind: "ecom_table", status, total: 2, succeeded: 1, failed: 1, common_params: {}, created_at: "", updated_at: "" },
  tasks: [
    { task_id: "b1-t0", row_index: 0, status: "done", video_url: "https://mock.local/v.mp4", error: null },
    { task_id: "b1-t1", row_index: 1, status: "failed", video_url: null, error: null, error_code: "BATCH_IMAGE_DOWNLOAD_FAILED", error_message: "外链图下载失败，请改用本地上传" }
  ]
});

beforeEach(() => {
  batchMock.data = detail("partial_failed");
  cancelMock.mutateAsync.mockResolvedValue({ batch_id: "b1", cancelled: 0, running: 1 });
});
afterEach(() => vi.clearAllMocks());

describe("BatchDetail (批次详情)", () => {
  it("渲染子任务：done 出成片/下载，failed 内联 error_message（承重·失败行内联）；v1 无单条重试按钮", () => {
    render(<BatchDetailView batchId="b1" onBack={vi.fn()} />);
    expect(screen.getByText("外链图下载失败，请改用本地上传")).toBeInTheDocument();
    expect(screen.getByText(copy.detail.download)).toBeInTheDocument();
    // v1 隐藏批内单条重试（后端无该端点）。
    expect(screen.queryByRole("button", { name: new RegExp(copy.batch.taskRetry) })).not.toBeInTheDocument();
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
