import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { BatchCommon } from "@/lib/api/types";

const createMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
vi.mock("@/lib/api/hooks", () => ({ useCreateBatch: () => createMock }));
// 子组件占位标记，隔离表单编排承重（各有专测/复用件）。
vi.mock("@/components/workbench/reference-images-picker", () => ({
  ReferenceImagesPicker: ({ onChange }: { onChange: (ids: string[]) => void }) => (
    <button type="button" onClick={() => onChange(["r1", "r2"])}>set-refs</button>
  )
}));
vi.mock("@/components/batch/common-params", () => ({
  CommonParams: ({ onChange }: { onChange: (c: BatchCommon) => void }) => (
    <button type="button" onClick={() => onChange({ video_mode: "video_gen", duration_sec: 5, resolution: "720p", apply_visible_label: true })}>
      set-common
    </button>
  )
}));
vi.mock("@/components/batch/batch-estimate-dialog", () => ({
  BatchEstimateDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => void }) =>
    open ? <button type="button" onClick={onConfirm}>confirm-batch</button> : null
}));

import { PromptSetForm } from "./prompt-set-form";

const typePrompts = (v: string) => fireEvent.change(screen.getByLabelText(new RegExp(copy.batch.promptLabel.slice(0, 3))), { target: { value: v } });

beforeEach(() => {
  createMock.mutateAsync.mockReset();
  createMock.mutateAsync.mockResolvedValue({ batch_id: "batch-1", task_ids: ["t0", "t1"] });
});
afterEach(() => vi.clearAllMocks());

describe("PromptSetForm (批量·提示词组)", () => {
  it("去空行 + 计数：3 行含空行 → 2 条", () => {
    render(<PromptSetForm onCreated={vi.fn()} />);
    typePrompts("赛博夜景\n\n樱花街道");
    expect(screen.getByText(copy.batch.promptCount(2))).toBeInTheDocument();
  });

  it(">30 真拦截：31 行 → 显式提示 + 生成禁用 + 不发请求（承重，不裁剪）", async () => {
    render(<PromptSetForm onCreated={vi.fn()} />);
    typePrompts(Array.from({ length: 31 }, (_, i) => `p${i}`).join("\n"));
    expect(screen.getByText(copy.batch.overLimitN(31))).toBeInTheDocument();
    fireEvent.click(screen.getByText("set-common"));
    const generate = screen.getByRole("button", { name: copy.workbench.generate });
    expect(generate).toBeDisabled(); // 超限禁用
    fireEvent.click(generate); // 即便强点也不开确认窗/不发请求
    expect(screen.queryByText("confirm-batch")).not.toBeInTheDocument();
    expect(createMock.mutateAsync).not.toHaveBeenCalled();
  });

  it("提交体逐字段 + 参考图 + apply_visible_label 透传（承重）", async () => {
    render(<PromptSetForm onCreated={vi.fn()} />);
    typePrompts("赛博夜景\n樱花街道");
    fireEvent.click(screen.getByText("set-refs"));
    fireEvent.click(screen.getByText("set-common"));
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.generate }));
    fireEvent.click(await screen.findByText("confirm-batch"));
    await waitFor(() => expect(createMock.mutateAsync).toHaveBeenCalledTimes(1));
    expect(createMock.mutateAsync.mock.calls[0][0]).toEqual({
      kind: "prompt_set",
      rows: [{ prompt: "赛博夜景" }, { prompt: "樱花街道" }],
      common: {
        video_mode: "video_gen",
        duration_sec: 5,
        resolution: "720p",
        apply_visible_label: true,
        reference_image_asset_ids: ["r1", "r2"]
      }
    });
  });

  it("空提示词：生成禁用", () => {
    render(<PromptSetForm onCreated={vi.fn()} />);
    expect(screen.getByRole("button", { name: copy.workbench.generate })).toBeDisabled();
  });
});
