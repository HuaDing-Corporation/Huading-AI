import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { BatchCommon } from "@/lib/api/types";
import type { EcomRowDraft } from "@/lib/batch/ecom-table";

const createMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const parseMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/hooks", () => ({
  useCreateBatch: () => createMock,
  useUploadImage: () => uploadMock
}));
// 保留 validateEcomRow / toEcomTableRow / BATCH_MAX_ROWS 真实（承重的校验+映射），仅 mock 解析。
vi.mock("@/lib/batch/ecom-table", async (orig) => ({
  ...(await orig<typeof import("@/lib/batch/ecom-table")>()),
  parseEcomTable: parseMock
}));
vi.mock("@/components/batch/common-params", () => ({
  CommonParams: ({ onChange }: { onChange: (c: BatchCommon) => void }) => (
    <>
      <button type="button" onClick={() => onChange({ video_mode: "seedance_i2v", duration_sec: 30, resolution: "720p", apply_visible_label: true })}>
        set-common
      </button>
      <button type="button" onClick={() => onChange({ video_mode: "seedance_i2v", duration_sec: 0, resolution: "720p", apply_visible_label: false })}>
        set-bad-duration
      </button>
    </>
  )
}));
vi.mock("@/components/batch/batch-estimate-dialog", () => ({
  BatchEstimateDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => void }) =>
    open ? <button type="button" onClick={onConfirm}>confirm-batch</button> : null
}));

import { EcomTableForm } from "./ecom-table-form";

const uploadFile = () => {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["x"], "t.xlsx")] } });
};
const validRows: EcomRowDraft[] = [
  { product_name: "保温杯", selling_points: "316 不锈钢", image_url: "http://x/1.png" },
  { product_name: "雨伞", selling_points: "自动折叠", image_url: "http://x/2.png" }
];

beforeEach(() => {
  createMock.mutateAsync.mockReset();
  createMock.mutateAsync.mockResolvedValue({ batch_id: "batch-1", task_ids: ["a", "b"] });
  parseMock.mockReset();
});
afterEach(() => vi.clearAllMocks());

describe("EcomTableForm (批量·商品表)", () => {
  it("上传解析 → 预览表渲染各行", async () => {
    parseMock.mockResolvedValue(validRows);
    render(<EcomTableForm onCreated={vi.fn()} />);
    uploadFile();
    expect(await screen.findByText("保温杯")).toBeInTheDocument();
    expect(screen.getByText("雨伞")).toBeInTheDocument();
  });

  it("行内校验：缺必填行标红 + 状态「缺必填」+ 生成禁用（承重·失败行内联）", async () => {
    parseMock.mockResolvedValue([{ product_name: "", selling_points: "卖点", image_url: "http://x/1.png" }]);
    render(<EcomTableForm onCreated={vi.fn()} />);
    uploadFile();
    await waitFor(() => expect(screen.getByText(copy.batch.ecomRowError)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: copy.workbench.generate })).toBeDisabled();
  });

  it(">30 真拦截：31 行全部预览(不裁剪) + 显式提示 + 生成禁用（承重）", async () => {
    parseMock.mockResolvedValue(Array.from({ length: 31 }, (_, i) => ({ product_name: `p${i}`, selling_points: "s", image_url: "http://x/1.png" })));
    render(<EcomTableForm onCreated={vi.fn()} />);
    uploadFile();
    expect(await screen.findByText(copy.batch.overLimitN(31))).toBeInTheDocument();
    expect(screen.getByText("p30")).toBeInTheDocument(); // 不裁剪，第 31 行也在预览
    fireEvent.click(screen.getByText("set-common"));
    expect(screen.getByRole("button", { name: copy.workbench.generate })).toBeDisabled();
  });

  it("每行图上传：无 URL 行缺图 → 上传本地图 → asset_id 回填，行变有效（承重）", async () => {
    parseMock.mockResolvedValue([{ product_name: "杯", selling_points: "钢", image_url: undefined }]);
    uploadMock.mutateAsync.mockResolvedValue({ asset_id: "asset-9" });
    render(<EcomTableForm onCreated={vi.fn()} />);
    uploadFile();
    await screen.findByText("杯");
    expect(screen.getByText(copy.batch.ecomRowError)).toBeInTheDocument(); // 缺图→缺必填
    // 点行内「上传本地图」→ 触发行 file input(accept 图片)→ 上传 → asset_id 回填
    fireEvent.click(screen.getByRole("button", { name: new RegExp(copy.batch.ecomRowImageUpload) }));
    const rowInput = document.querySelector('input[accept*="image/"]') as HTMLInputElement;
    fireEvent.change(rowInput, { target: { files: [new File(["x"], "p.png", { type: "image/png" })] } });
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(copy.batch.ecomRowOk)).toBeInTheDocument()); // 回填后就绪
  });

  it("非法自定义时长(0) → 即便全行有效也禁用生成（承重·对齐单条 i2v 时长门控）", async () => {
    parseMock.mockResolvedValue(validRows);
    render(<EcomTableForm onCreated={vi.fn()} />);
    uploadFile();
    await screen.findByText("保温杯");
    fireEvent.click(screen.getByText("set-bad-duration"));
    expect(screen.getByRole("button", { name: copy.workbench.generate })).toBeDisabled();
  });

  it("全行有效 → 生成 → 提交体逐字段 + apply_visible_label 透传（承重）", async () => {
    parseMock.mockResolvedValue(validRows);
    render(<EcomTableForm onCreated={vi.fn()} />);
    uploadFile();
    await screen.findByText("保温杯");
    fireEvent.click(screen.getByText("set-common"));
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.generate }));
    fireEvent.click(await screen.findByText("confirm-batch"));
    await waitFor(() => expect(createMock.mutateAsync).toHaveBeenCalledTimes(1));
    expect(createMock.mutateAsync.mock.calls[0][0]).toEqual({
      kind: "ecom_table",
      rows: [
        { product_name: "保温杯", selling_points: "316 不锈钢", image_url: "http://x/1.png" },
        { product_name: "雨伞", selling_points: "自动折叠", image_url: "http://x/2.png" }
      ],
      common: { video_mode: "seedance_i2v", duration_sec: 30, resolution: "720p", apply_visible_label: true }
    });
  });
});
