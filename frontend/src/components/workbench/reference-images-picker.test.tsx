import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
vi.mock("@/lib/api/hooks", () => ({
  useUploadImage: () => ({ mutateAsync: uploadMock.mutateAsync, isPending: uploadMock.isPending })
}));

import { ReferenceImagesPicker } from "./reference-images-picker";

const file = (n: string) => new File(["x"], `${n}.png`, { type: "image/png" });
const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement;

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  let n = 0;
  uploadMock.isPending = false;
  uploadMock.mutateAsync.mockImplementation(() => Promise.resolve({ asset_id: `a${++n}`, type: "avatar_image", status: "ready" }));
});
afterEach(() => vi.clearAllMocks());

describe("ReferenceImagesPicker (参考图 ≤9 承重)", () => {
  it("上传 10 张 → 仅添加 9 张 + 超限提示 + onChange 上抛 9 个 asset_id", async () => {
    const onChange = vi.fn();
    render(<ReferenceImagesPicker onChange={onChange} />);
    fireEvent.change(fileInput(), { target: { files: Array.from({ length: 10 }, (_, i) => file(`r${i}`)) } });

    // 承重：最多 9 张被上传/渲染（第 10 张不上传）。
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(9));
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(9));
    expect(screen.getByText(copy.workbench.vgRefOverLimit)).toBeInTheDocument();
    expect(onChange).toHaveBeenLastCalledWith(["a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "a9"]);
  });

  it("移除一张 → 数量减少且 onChange 同步", async () => {
    const onChange = vi.fn();
    render(<ReferenceImagesPicker onChange={onChange} />);
    fireEvent.change(fileInput(), { target: { files: [file("a"), file("b"), file("c")] } });
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(3));

    fireEvent.click(screen.getAllByRole("button", { name: copy.workbench.removeImage })[0]);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(2));
    expect(onChange).toHaveBeenLastCalledWith(["a2", "a3"]);
  });

  it("满 9 张后添加按钮禁用", async () => {
    render(<ReferenceImagesPicker onChange={vi.fn()} />);
    fireEvent.change(fileInput(), { target: { files: Array.from({ length: 9 }, (_, i) => file(`f${i}`)) } });
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(9));
    // 添加按钮（最后一个 button）禁用。
    const addBtn = screen.getByRole("button", { name: new RegExp(copy.workbench.vgRefImagesUpload) });
    expect(addBtn).toBeDisabled();
  });

  it("增量累加越限：先 7 张再 5 张 → 恰 9 张 + 超限提示（room 用最新长度，非陈旧闭包）", async () => {
    const onChange = vi.fn();
    render(<ReferenceImagesPicker onChange={onChange} />);
    fireEvent.change(fileInput(), { target: { files: Array.from({ length: 7 }, (_, i) => file(`p${i}`)) } });
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(7));

    // 再投 5 张：剩余位仅 2 → 仅 2 张被上传，总数封顶 9，出现超限提示。
    fireEvent.change(fileInput(), { target: { files: Array.from({ length: 5 }, (_, i) => file(`q${i}`)) } });
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(9));
    expect(screen.getByText(copy.workbench.vgRefOverLimit)).toBeInTheDocument();
    expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(9); // 7 + 2，第 8 张起被剩余位拦
    // items 已渲染不代表上抛 items 的 passive effect 也已执行；全仓高负载下同步读取会偶发停在上一拍的 7。
    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith(["a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "a9"])
    );
  });

  it("上传失败：catch 友好提示，失败项不计入", async () => {
    const onChange = vi.fn();
    uploadMock.mutateAsync.mockReset().mockRejectedValue(new Error("boom"));
    render(<ReferenceImagesPicker onChange={onChange} />);
    fireEvent.change(fileInput(), { target: { files: [file("a")] } });
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.queryAllByRole("img")).toHaveLength(0);
  });

  it("非法类型 → 友好提示，不上传", async () => {
    render(<ReferenceImagesPicker onChange={vi.fn()} />);
    fireEvent.change(fileInput(), { target: { files: [new File(["x"], "a.txt", { type: "text/plain" })] } });
    await waitFor(() => expect(screen.getByText(copy.errors.uploadType)).toBeInTheDocument());
    expect(uploadMock.mutateAsync).not.toHaveBeenCalled();
  });
});
