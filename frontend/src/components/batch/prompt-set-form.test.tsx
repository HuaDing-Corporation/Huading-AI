import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { BatchCommon } from "@/lib/api/types";

const createMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
vi.mock("@/lib/api/hooks", () => ({ useCreateBatch: () => createMock }));
// 子组件占位标记，隔离表单编排承重（各有专测/复用件）。参考图 mock 经 onItemsChange 上抛有序 {assetId,preview}。
type RefItem = { assetId: string; preview: string };
vi.mock("@/components/workbench/reference-images-picker", () => ({
  ReferenceImagesPicker: ({ onItemsChange }: { onItemsChange?: (items: RefItem[]) => void }) => (
    <>
      <button type="button" onClick={() => onItemsChange?.([{ assetId: "r1", preview: "blob:r1" }, { assetId: "r2", preview: "blob:r2" }])}>set-refs</button>
      <button type="button" onClick={() => onItemsChange?.([{ assetId: "r1", preview: "blob:r1" }])}>set-one-ref</button>
    </>
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
// 具名查询：真栈页面 CommonParams 还含 AI 标识开关（亦 role=switch），按可达名锁定配对开关，防未来脆化。
const pairingSwitch = () => screen.getByRole("switch", { name: copy.batch.pairToggleLabel });
const togglePairing = () => fireEvent.click(pairingSwitch());
const generate = () => screen.getByRole("button", { name: copy.workbench.generate });

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
    expect(generate()).toBeDisabled(); // 超限禁用
    fireEvent.click(generate()); // 即便强点也不开确认窗/不发请求
    expect(screen.queryByText("confirm-batch")).not.toBeInTheDocument();
    expect(createMock.mutateAsync).not.toHaveBeenCalled();
  });

  it("共享模式（默认关）：补一句「应用于每条」+ 提交体 rows 仅 prompt + common.reference_image_asset_ids（承重·共享回落）", async () => {
    render(<PromptSetForm onCreated={vi.fn()} />);
    expect(screen.getByText(copy.batch.pairSharedNote)).toBeInTheDocument(); // 消歧提示
    typePrompts("赛博夜景\n樱花街道");
    fireEvent.click(screen.getByText("set-refs"));
    fireEvent.click(screen.getByText("set-common"));
    fireEvent.click(generate());
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

  it("配对模式：第 N 行↔第 N 图 按序映射，提交体每行 {prompt, image_asset_id}，common 不带 reference_image_asset_ids（承重·打乱即红）", async () => {
    render(<PromptSetForm onCreated={vi.fn()} />);
    typePrompts("赛博夜景\n樱花街道");
    fireEvent.click(screen.getByText("set-refs")); // r1, r2 有序
    togglePairing();
    fireEvent.click(screen.getByText("set-common"));
    fireEvent.click(generate());
    fireEvent.click(await screen.findByText("confirm-batch"));
    await waitFor(() => expect(createMock.mutateAsync).toHaveBeenCalledTimes(1));
    const body = createMock.mutateAsync.mock.calls[0][0];
    expect(body.rows).toEqual([
      { prompt: "赛博夜景", image_asset_id: "r1" }, // 第 1 行 ↔ 第 1 图
      { prompt: "樱花街道", image_asset_id: "r2" } // 第 2 行 ↔ 第 2 图
    ]);
    expect(body.common.reference_image_asset_ids).toBeUndefined(); // 配对不再走共享字段
  });

  it("配对模式·数量不等：2 行提示词 + 1 张图 → 显式提示 + 生成禁用 + 不发请求（承重）", async () => {
    render(<PromptSetForm onCreated={vi.fn()} />);
    typePrompts("赛博夜景\n樱花街道");
    fireEvent.click(screen.getByText("set-one-ref")); // 仅 1 张
    togglePairing();
    fireEvent.click(screen.getByText("set-common"));
    expect(screen.getByText(copy.batch.pairCountMismatch(2, 1))).toBeInTheDocument();
    expect(generate()).toBeDisabled();
    fireEvent.click(generate());
    expect(screen.queryByText("confirm-batch")).not.toBeInTheDocument();
    expect(createMock.mutateAsync).not.toHaveBeenCalled();
  });

  it("配对模式·配对预览：渲染 行号 + 缩略图 + 提示词摘要 供核对顺序", () => {
    render(<PromptSetForm onCreated={vi.fn()} />);
    typePrompts("赛博夜景\n樱花街道");
    fireEvent.click(screen.getByText("set-refs"));
    togglePairing();
    expect(screen.getByText(copy.batch.pairPreviewTitle)).toBeInTheDocument();
    expect(screen.getByText(copy.batch.pairRowLabel(1))).toBeInTheDocument();
    expect(screen.getByText(copy.batch.pairRowLabel(2))).toBeInTheDocument();
    expect(screen.getByText("赛博夜景")).toBeInTheDocument();
    // 缩略图按序：第 1 行用 r1 预览，第 2 行用 r2 预览。
    const thumbs = screen.getAllByAltText(new RegExp("第 \\d 行")) as HTMLImageElement[];
    expect(thumbs.map((t) => t.getAttribute("src"))).toEqual(["blob:r1", "blob:r2"]);
  });

  it("配对模式·数量不等（反向：图多于行 1 行 2 图）→ 禁用 + 提示（承重·双向门控）", () => {
    render(<PromptSetForm onCreated={vi.fn()} />);
    typePrompts("只有一行");
    fireEvent.click(screen.getByText("set-refs")); // 2 图
    togglePairing();
    expect(screen.getByText(copy.batch.pairCountMismatch(1, 2))).toBeInTheDocument();
    expect(generate()).toBeDisabled();
  });

  it("配对模式·未传图（0 图 2 行）→ 禁用 + 提示 + 预览每行「缺图」占位（承重·空图分支）", () => {
    render(<PromptSetForm onCreated={vi.fn()} />);
    typePrompts("赛博夜景\n樱花街道");
    togglePairing(); // 不点任何 set-refs → refItems=[]
    expect(screen.getByText(copy.batch.pairCountMismatch(2, 0))).toBeInTheDocument();
    expect(generate()).toBeDisabled();
    expect(screen.getAllByText(copy.batch.pairMissingImage)).toHaveLength(2); // 两行皆缺图占位
  });

  it("配对模式·动态重算：就绪(2 行 2 图)后加到 3 行 → 转禁用 + 提示 + 第 3 行「缺图」（承重·联动重算）", () => {
    render(<PromptSetForm onCreated={vi.fn()} />);
    typePrompts("赛博夜景\n樱花街道");
    fireEvent.click(screen.getByText("set-refs")); // 2 图
    togglePairing();
    expect(generate()).toBeEnabled(); // 齐活：可生成
    typePrompts("赛博夜景\n樱花街道\n星空草原"); // 加到 3 行 → 派生门控须随之重算
    expect(screen.getByText(copy.batch.pairCountMismatch(3, 2))).toBeInTheDocument();
    expect(generate()).toBeDisabled();
    expect(screen.getByText(copy.batch.pairMissingImage)).toBeInTheDocument(); // 第 3 行缺图占位
  });

  it("配对开关往返（关→开→关）→ 共享回落：提交体 rows 无 image_asset_id + common 带 reference_image_asset_ids（承重·回落纯净）", async () => {
    render(<PromptSetForm onCreated={vi.fn()} />);
    typePrompts("赛博夜景\n樱花街道");
    fireEvent.click(screen.getByText("set-refs"));
    togglePairing(); // 开
    togglePairing(); // 关（回到共享）
    fireEvent.click(screen.getByText("set-common"));
    fireEvent.click(generate());
    fireEvent.click(await screen.findByText("confirm-batch"));
    await waitFor(() => expect(createMock.mutateAsync).toHaveBeenCalledTimes(1));
    const body = createMock.mutateAsync.mock.calls[0][0];
    expect(body.rows).toEqual([{ prompt: "赛博夜景" }, { prompt: "樱花街道" }]); // 无残留逐行 image_asset_id
    expect(body.common.reference_image_asset_ids).toEqual(["r1", "r2"]); // 回落共享字段
  });

  it("空提示词：生成禁用", () => {
    render(<PromptSetForm onCreated={vi.fn()} />);
    expect(generate()).toBeDisabled();
  });
});
