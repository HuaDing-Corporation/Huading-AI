import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { BatchEstimateResponse, BatchRequest } from "@/lib/api/types";

const estimateMock = vi.hoisted(() => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, data: undefined as BatchEstimateResponse | undefined }));
vi.mock("@/lib/api/hooks", () => ({ useEstimateBatch: () => estimateMock }));

import { BatchEstimateDialog } from "./batch-estimate-dialog";

const req = (resolution = "720p"): BatchRequest => ({
  kind: "prompt_set",
  rows: [{ prompt: "a" }, { prompt: "b" }],
  common: { resolution }
});
const est = (over: boolean): BatchEstimateResponse => ({
  total_rows: 2,
  per_row_credits: 5,
  total_credits: over ? 200 : 10,
  insufficient: over,
  balance_credits: 50
});

beforeEach(() => {
  estimateMock.isPending = false;
  estimateMock.data = undefined;
});
afterEach(() => vi.clearAllMocks());

describe("BatchEstimateDialog (批量确认)", () => {
  it("打开时请求预估，展示总条数/单条/总积分/余额", () => {
    estimateMock.data = est(false);
    render(<BatchEstimateDialog open request={req()} submitting={false} onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(estimateMock.mutate).toHaveBeenCalledWith(req());
    expect(screen.getByText(copy.batch.estimateRows(2))).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument(); // 总积分
    expect(screen.getByRole("button", { name: copy.batch.estimateConfirm })).toBeEnabled();
  });

  it("余额不足 → 确认禁用 + 提示（承重）", () => {
    estimateMock.data = est(true);
    render(<BatchEstimateDialog open request={req()} submitting={false} onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText(copy.batch.estimateInsufficient)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: copy.batch.estimateConfirm })).toBeDisabled();
  });

  it("充足 → 确认调 onConfirm", () => {
    estimateMock.data = est(false);
    const onConfirm = vi.fn();
    render(<BatchEstimateDialog open request={req()} submitting={false} onConfirm={onConfirm} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: copy.batch.estimateConfirm }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("1080p → 排队提示（且充足时）", () => {
    estimateMock.data = est(false);
    render(<BatchEstimateDialog open request={req("1080p")} submitting={false} onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText(copy.batch.estimateQueueHint)).toBeInTheDocument();
  });
});
