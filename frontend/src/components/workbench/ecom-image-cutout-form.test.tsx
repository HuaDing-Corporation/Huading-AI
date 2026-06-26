import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const cutoutMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const cutoutBatchMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const trackExistingMock = vi.hoisted(() => vi.fn());
const tasksMock = vi.hoisted(() => ({ tasks: [] as Array<Record<string, unknown>> }));

vi.mock("@/lib/api/hooks", () => ({
  useUploadImage: () => ({ mutateAsync: uploadMock.mutateAsync, isPending: uploadMock.isPending }),
  useCutoutImage: () => ({ mutateAsync: cutoutMock.mutateAsync, isPending: cutoutMock.isPending }),
  useCutoutBatch: () => ({ mutateAsync: cutoutBatchMock.mutateAsync, isPending: cutoutBatchMock.isPending })
}));
vi.mock("@/lib/videos/tasks-context", () => ({
  useVideoTasks: () => ({ tasks: tasksMock.tasks, trackExisting: trackExistingMock })
}));

import { EcomImageCutoutForm } from "./ecom-image-cutout-form";

const png = (name: string) => new File(["x"], name, { type: "image/png" });
const doneTask = (id: string, url: string) => ({
  taskId: id, status: "done", progress: 100, statusLabel: "已完成",
  playbackUrl: url, downloadUrl: `${url}?dl=1`, topic: "x", mode: "photo", retryable: false
});

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  uploadMock.isPending = false;
  cutoutMock.isPending = false;
  cutoutBatchMock.isPending = false;
  tasksMock.tasks = [];
  uploadMock.mutateAsync.mockResolvedValue({ asset_id: "asset-1" });
  cutoutMock.mutateAsync.mockResolvedValue({ task_id: "t-1", status: "queued" });
  cutoutBatchMock.mutateAsync.mockResolvedValue({
    batch_id: "b-1",
    tasks: [
      { task_id: "t-1", source_asset_id: "asset-1", status: "queued" },
      { task_id: "t-2", source_asset_id: "asset-1", status: "queued" }
    ]
  });
});
afterEach(() => vi.clearAllMocks());

describe("EcomImageCutoutForm (电商图 · 白底图/抠图)", () => {
  it("单张未上传：生成按钮禁用", () => {
    render(<EcomImageCutoutForm />);
    expect(screen.getByRole("button", { name: "生成" })).toBeDisabled();
  });

  it("单张：上传 → 生成 → cutout 提交 {source_asset_id,background} + trackExisting + 结果图", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/cut.png")];
    render(<EcomImageCutoutForm />);

    fireEvent.change(document.querySelector("#ecom-cutout-source")!, { target: { files: [png("p.png")] } });
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() =>
      expect(cutoutMock.mutateAsync).toHaveBeenCalledWith({ source_asset_id: "asset-1", background: "white" })
    );
    expect(trackExistingMock).toHaveBeenCalledWith("t-1", expect.any(String), "photo");
    expect(await screen.findByRole("img", { name: copy.workbench.ecomResultsLabel })).toHaveAttribute(
      "src",
      "https://mock.local/cut.png"
    );
  });

  it("透明底：cutout 提交 background=transparent", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/cut.png")];
    render(<EcomImageCutoutForm />);
    fireEvent.change(document.querySelector("#ecom-cutout-source")!, { target: { files: [png("p.png")] } });
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "透明底" }));
    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() =>
      expect(cutoutMock.mutateAsync).toHaveBeenCalledWith({ source_asset_id: "asset-1", background: "transparent" })
    );
  });

  it("批量：多图上传 → 生成 → cutoutBatch fan-out(items×N)+ trackExisting×N + 批量下载", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/1.png"), doneTask("t-2", "https://mock.local/2.png")];
    render(<EcomImageCutoutForm />);

    fireEvent.click(screen.getByRole("button", { name: "批量" }));
    fireEvent.change(document.querySelector("#ecom-cutout-batch")!, {
      target: { files: [png("a.png"), png("b.png")] }
    });
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => expect(cutoutBatchMock.mutateAsync).toHaveBeenCalledTimes(1));
    expect(cutoutBatchMock.mutateAsync.mock.calls[0][0]).toEqual({
      items: [
        { source_asset_id: "asset-1", background: "white" },
        { source_asset_id: "asset-1", background: "white" }
      ]
    });
    expect(trackExistingMock).toHaveBeenCalledTimes(2);
    // 批量下载入口(N>1 且有 done)+ 点击逐个触发下载(2 done → click 2 次)
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    fireEvent.click(await screen.findByRole("button", { name: /批量下载/ }));
    expect(clickSpy).toHaveBeenCalledTimes(2);
    clickSpy.mockRestore();
  });

  it("批量单失败隔离：1 done 出图、1 failed 出友好错误，互不连累", async () => {
    tasksMock.tasks = [
      doneTask("t-1", "https://mock.local/1.png"),
      { taskId: "t-2", status: "failed", progress: 0, statusLabel: "失败", errorCode: "IMAGE_MODERATION_BLOCKED", topic: "x", mode: "photo", retryable: false }
    ];
    render(<EcomImageCutoutForm />);
    fireEvent.click(screen.getByRole("button", { name: "批量" }));
    fireEvent.change(document.querySelector("#ecom-cutout-batch")!, {
      target: { files: [png("a.png"), png("b.png")] }
    });
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => expect(cutoutBatchMock.mutateAsync).toHaveBeenCalled());

    // done 出图(t-1)
    expect(await screen.findByRole("img", { name: copy.workbench.ecomResultsLabel })).toHaveAttribute(
      "src",
      "https://mock.local/1.png"
    );
    // failed 出友好错误(t-2，friendlyImageError 映射 IMAGE_MODERATION_BLOCKED → 区分性文案)
    expect(screen.getByText(copy.errors.imageModeration)).toBeInTheDocument();
  });

  it("防连点：提交中按钮显「生成中…」并禁用", () => {
    cutoutMock.isPending = true;
    render(<EcomImageCutoutForm />);
    expect(screen.getByRole("button", { name: "生成中…" })).toBeDisabled();
  });
});
