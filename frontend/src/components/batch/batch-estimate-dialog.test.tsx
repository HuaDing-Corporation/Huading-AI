import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { BatchEstimateResponse, BatchRequest } from "@/lib/api/types";

const estimateMock = vi.hoisted(() => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, data: undefined as BatchEstimateResponse | undefined }));
vi.mock("@/lib/api/hooks", () => ({
  useEstimateBatch: () => estimateMock,
  useVoices: () => ({
    data: [
      { id: "v1", provider: "edge_tts", voice_code: "x", display_name: "知性女声", gender: "female", language: "zh-CN", source: "preset" },
      { id: "v2", provider: "edge_tts", voice_code: "y", display_name: "磁性男声", gender: "male", language: "zh-CN", source: "preset" }
    ]
  })
}));

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

  it("商品表：按 voice_id 精确查名（指向非首项 v2 → 显示磁性男声，而非首项知性女声）", () => {
    estimateMock.data = est(false);
    // 指向列表第二项：杀死「取 data[0].display_name」退化实现（只放单音色时无法证伪）。
    const ecomReq: BatchRequest = { kind: "ecom_table", rows: [{ product_name: "a", selling_points: "s", image_url: "u" }], common: { video_mode: "seedance_i2v", resolution: "720p", voice_id: "v2" } };
    render(<BatchEstimateDialog open request={ecomReq} submitting={false} onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText(copy.batch.estimateVoice)).toBeInTheDocument();
    expect(screen.getByText("磁性男声")).toBeInTheDocument();
    expect(screen.queryByText("知性女声")).not.toBeInTheDocument();
  });

  it("1080p → 排队提示（且充足时）", () => {
    estimateMock.data = est(false);
    render(<BatchEstimateDialog open request={req("1080p")} submitting={false} onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText(copy.batch.estimateQueueHint)).toBeInTheDocument();
  });
});
