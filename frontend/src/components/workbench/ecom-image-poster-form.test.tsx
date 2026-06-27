import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const posterMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const posterBatchMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const templatesMock = vi.hoisted(() => ({ data: [] as Array<{ id: string; name: string }>, isLoading: false, isError: false }));
const trackExistingMock = vi.hoisted(() => vi.fn());
const tasksMock = vi.hoisted(() => ({ tasks: [] as Array<Record<string, unknown>> }));

vi.mock("@/lib/api/hooks", () => ({
  useUploadImage: () => ({ mutateAsync: uploadMock.mutateAsync, isPending: uploadMock.isPending }),
  usePosterImage: () => ({ mutateAsync: posterMock.mutateAsync, isPending: posterMock.isPending }),
  usePosterBatch: () => ({ mutateAsync: posterBatchMock.mutateAsync, isPending: posterBatchMock.isPending }),
  usePosterTemplates: () => ({ data: templatesMock.data, isLoading: templatesMock.isLoading, isError: templatesMock.isError })
}));
vi.mock("@/lib/videos/tasks-context", () => ({
  useVideoTasks: () => ({ tasks: tasksMock.tasks, trackExisting: trackExistingMock })
}));

import { EcomImagePosterForm } from "./ecom-image-poster-form";

const png = (name: string) => new File(["x"], name, { type: "image/png" });
const doneTask = (id: string, url: string) => ({
  taskId: id, status: "done", progress: 100, statusLabel: "已完成",
  playbackUrl: url, downloadUrl: `${url}?dl=1`, topic: "x", mode: "photo", retryable: false
});
const TEMPLATES = [
  { id: "promo", name: "大促爆款" },
  { id: "festive", name: "节日喜庆" }
];

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  uploadMock.isPending = false;
  posterMock.isPending = false;
  posterBatchMock.isPending = false;
  templatesMock.data = TEMPLATES;
  templatesMock.isLoading = false;
  templatesMock.isError = false;
  tasksMock.tasks = [];
  uploadMock.mutateAsync.mockResolvedValue({ asset_id: "asset-1" });
  posterMock.mutateAsync.mockResolvedValue({ task_id: "t-1", status: "queued" });
  posterBatchMock.mutateAsync.mockResolvedValue({
    batch_id: "b-1",
    tasks: [
      { task_id: "t-1", source_asset_id: "asset-1", status: "queued" },
      { task_id: "t-2", source_asset_id: "asset-1", status: "queued" }
    ]
  });
});
afterEach(() => vi.clearAllMocks());

describe("EcomImagePosterForm (电商图 · 营销海报)", () => {
  it("版式必填门：上传后未选版式仍禁用生成", async () => {
    render(<EcomImagePosterForm />);
    fireEvent.change(document.querySelector("#ecom-poster-source")!, { target: { files: [png("p.png")] } });
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "生成" })).toBeDisabled();
  });

  it("单张：上传 + 选版式 → 生成 → poster 提交 {source_asset_id,template_id} + trackExisting + 结果图", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/poster-promo.png")];
    render(<EcomImagePosterForm />);

    fireEvent.change(document.querySelector("#ecom-poster-source")!, { target: { files: [png("p.png")] } });
    fireEvent.click(screen.getByRole("button", { name: "大促爆款" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() =>
      expect(posterMock.mutateAsync).toHaveBeenCalledWith({
        source_asset_id: "asset-1",
        template_id: "promo",
        title: "",
        subtitle: ""
      })
    );
    expect(trackExistingMock).toHaveBeenCalledWith("t-1", expect.any(String), "photo");
    expect(await screen.findByRole("img", { name: copy.workbench.ecomResultsLabel })).toHaveAttribute(
      "src",
      "https://mock.local/poster-promo.png"
    );
  });

  it("标题 + 自定义一行：提交带 title / subtitle", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/poster-promo.png")];
    render(<EcomImagePosterForm />);

    fireEvent.change(document.querySelector("#ecom-poster-source")!, { target: { files: [png("p.png")] } });
    fireEvent.click(screen.getByRole("button", { name: "大促爆款" }));
    fireEvent.change(screen.getByLabelText(/标题/), { target: { value: "年中大促 全场5折" } });
    fireEvent.change(screen.getByLabelText(/自定义一行/), { target: { value: "限时3天 错过再等一年" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() =>
      expect(posterMock.mutateAsync).toHaveBeenCalledWith({
        source_asset_id: "asset-1",
        template_id: "promo",
        title: "年中大促 全场5折",
        subtitle: "限时3天 错过再等一年"
      })
    );
  });

  it("标题 ≤30 / 一行 ≤40：超长输入被截断", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/poster-promo.png")];
    render(<EcomImagePosterForm />);

    fireEvent.change(document.querySelector("#ecom-poster-source")!, { target: { files: [png("p.png")] } });
    fireEvent.click(screen.getByRole("button", { name: "大促爆款" }));
    fireEvent.change(screen.getByLabelText(/标题/), { target: { value: "标".repeat(50) } });
    fireEvent.change(screen.getByLabelText(/自定义一行/), { target: { value: "行".repeat(60) } });
    expect(screen.getByText("30/30")).toBeInTheDocument();
    expect(screen.getByText("40/40")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => expect(posterMock.mutateAsync).toHaveBeenCalled());
    const call = posterMock.mutateAsync.mock.calls[0][0];
    expect(call.title).toHaveLength(30);
    expect(call.subtitle).toHaveLength(40);
  });

  it("批量：多图 + 选版式 → 生成 → posterBatch fan-out(items×N)+ trackExisting×N + 批量下载", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/1.png"), doneTask("t-2", "https://mock.local/2.png")];
    render(<EcomImagePosterForm />);

    fireEvent.click(screen.getByRole("button", { name: "批量" }));
    fireEvent.change(document.querySelector("#ecom-poster-batch")!, { target: { files: [png("a.png"), png("b.png")] } });
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "大促爆款" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => expect(posterBatchMock.mutateAsync).toHaveBeenCalledTimes(1));
    expect(posterBatchMock.mutateAsync.mock.calls[0][0]).toEqual({
      items: [
        { source_asset_id: "asset-1", template_id: "promo", title: "", subtitle: "" },
        { source_asset_id: "asset-1", template_id: "promo", title: "", subtitle: "" }
      ]
    });
    expect(trackExistingMock).toHaveBeenCalledTimes(2);

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
    render(<EcomImagePosterForm />);

    fireEvent.click(screen.getByRole("button", { name: "批量" }));
    fireEvent.change(document.querySelector("#ecom-poster-batch")!, { target: { files: [png("a.png"), png("b.png")] } });
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "大促爆款" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => expect(posterBatchMock.mutateAsync).toHaveBeenCalled());

    expect(await screen.findByRole("img", { name: copy.workbench.ecomResultsLabel })).toHaveAttribute("src", "https://mock.local/1.png");
    expect(screen.getByText(copy.errors.imageModeration)).toBeInTheDocument();
  });

  it("版式加载中：出加载态，生成禁用", () => {
    templatesMock.data = [];
    templatesMock.isLoading = true;
    render(<EcomImagePosterForm />);
    expect(screen.getByText(copy.workbench.ecomTemplateLoading)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成" })).toBeDisabled();
  });

  it("版式加载失败：出错误态，生成禁用", () => {
    templatesMock.data = [];
    templatesMock.isError = true;
    render(<EcomImagePosterForm />);
    expect(screen.getByText(copy.workbench.ecomTemplateError)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成" })).toBeDisabled();
  });

  it("防连点：提交中按钮显「生成中…」并禁用", () => {
    posterMock.isPending = true;
    render(<EcomImagePosterForm />);
    expect(screen.getByRole("button", { name: "生成中…" })).toBeDisabled();
  });
});
