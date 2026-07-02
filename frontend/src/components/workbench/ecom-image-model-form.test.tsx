import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const modelMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const modelBatchMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const stylesMock = vi.hoisted(() => ({ data: [] as Array<{ id: string; name: string }>, isLoading: false, isError: false }));
const trackExistingMock = vi.hoisted(() => vi.fn());
const tasksMock = vi.hoisted(() => ({ tasks: [] as Array<Record<string, unknown>> }));

vi.mock("@/lib/api/hooks", () => ({
  useUploadImage: () => ({ mutateAsync: uploadMock.mutateAsync, isPending: uploadMock.isPending }),
  useModelImage: () => ({ mutateAsync: modelMock.mutateAsync, isPending: modelMock.isPending }),
  useModelBatch: () => ({ mutateAsync: modelBatchMock.mutateAsync, isPending: modelBatchMock.isPending }),
  useModelStyles: () => ({ data: stylesMock.data, isLoading: stylesMock.isLoading, isError: stylesMock.isError })
}));
vi.mock("@/lib/videos/tasks-context", () => ({
  useVideoTasks: () => ({ tasks: tasksMock.tasks, trackExisting: trackExistingMock })
}));

import { EcomImageModelForm } from "./ecom-image-model-form";

const png = (name: string) => new File(["x"], name, { type: "image/png" });
const doneTask = (id: string, url: string) => ({
  taskId: id, status: "done", progress: 100, statusLabel: "已完成",
  playbackUrl: url, downloadUrl: `${url}?dl=1`, topic: "x", mode: "photo", retryable: false
});
// 测试用风格无 thumbnail_url → 选项可读名仅为风格名（getByRole name 精确匹配）
const STYLES = [
  { id: "studio", name: "简约棚拍" },
  { id: "street", name: "街头实景" }
];

beforeEach(() => {
  window.localStorage.clear(); // 每用例干净起点：AI 标识开关默认关
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  uploadMock.isPending = false;
  modelMock.isPending = false;
  modelBatchMock.isPending = false;
  stylesMock.data = STYLES;
  stylesMock.isLoading = false;
  stylesMock.isError = false;
  tasksMock.tasks = [];
  uploadMock.mutateAsync.mockResolvedValue({ asset_id: "asset-1" });
  modelMock.mutateAsync.mockResolvedValue({ task_id: "t-1", status: "queued" });
  modelBatchMock.mutateAsync.mockResolvedValue({
    batch_id: "b-1",
    tasks: [
      { task_id: "t-1", source_asset_id: "asset-1", status: "queued" },
      { task_id: "t-2", source_asset_id: "asset-1", status: "queued" }
    ]
  });
});
afterEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear(); // 清 AI 标识开关记忆，隔离用例
});

describe("EcomImageModelForm (电商图 · AI 模特)", () => {
  it("风格必填门：上传后未选风格仍禁用生成", async () => {
    render(<EcomImageModelForm />);
    fireEvent.change(document.querySelector("#ecom-model-source")!, { target: { files: [png("p.png")] } });
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "生成" })).toBeDisabled();
  });

  it("单张：上传 + 选风格 → 生成 → model 提交 {source_asset_id,gender,style_id} + trackExisting + 结果图", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/model-studio.png")];
    render(<EcomImageModelForm />);

    fireEvent.change(document.querySelector("#ecom-model-source")!, { target: { files: [png("p.png")] } });
    fireEvent.click(screen.getByRole("button", { name: "简约棚拍" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() =>
      expect(modelMock.mutateAsync).toHaveBeenCalledWith({
        source_asset_id: "asset-1",
        gender: "female",
        style_id: "studio",
        extra_prompt: undefined,
        apply_visible_label: false
      })
    );
    expect(trackExistingMock).toHaveBeenCalledWith("t-1", expect.any(String), "photo", false);
    expect(await screen.findByRole("img", { name: copy.workbench.ecomResultsLabel })).toHaveAttribute(
      "src",
      "https://mock.local/model-studio.png"
    );
  });

  it("开启 AI 标识开关 → model 提交 apply_visible_label:true + trackExisting 第4参 true（承重）", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/model-studio.png")];
    render(<EcomImageModelForm />);
    fireEvent.change(document.querySelector("#ecom-model-source")!, { target: { files: [png("p.png")] } });
    fireEvent.click(screen.getByRole("button", { name: "简约棚拍" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());
    fireEvent.click(screen.getByRole("switch")); // 开启 AI 生成标识
    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() =>
      expect(modelMock.mutateAsync).toHaveBeenCalledWith({
        source_asset_id: "asset-1",
        gender: "female",
        style_id: "studio",
        extra_prompt: undefined,
        apply_visible_label: true
      })
    );
    expect(trackExistingMock).toHaveBeenCalledWith("t-1", expect.any(String), "photo", true);
  });

  it("性别 + 自定义补充：提交带 gender=male、extra_prompt(对齐后端字段名)", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/model-studio.png")];
    render(<EcomImageModelForm />);

    fireEvent.change(document.querySelector("#ecom-model-source")!, { target: { files: [png("p.png")] } });
    fireEvent.click(screen.getByRole("button", { name: "简约棚拍" }));
    fireEvent.click(screen.getByRole("button", { name: "男" }));
    fireEvent.change(screen.getByLabelText(/自定义补充/), { target: { value: "暖光街头微笑站姿" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() =>
      expect(modelMock.mutateAsync).toHaveBeenCalledWith({
        source_asset_id: "asset-1",
        gender: "male",
        style_id: "studio",
        extra_prompt: "暖光街头微笑站姿",
        apply_visible_label: false
      })
    );
  });

  it("gender=不限(any)：提交带 gender=any", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/model-studio.png")];
    render(<EcomImageModelForm />);

    fireEvent.change(document.querySelector("#ecom-model-source")!, { target: { files: [png("p.png")] } });
    fireEvent.click(screen.getByRole("button", { name: "简约棚拍" }));
    fireEvent.click(screen.getByRole("button", { name: "不限" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => expect(modelMock.mutateAsync).toHaveBeenCalled());
    expect(modelMock.mutateAsync.mock.calls[0][0]).toMatchObject({ gender: "any", style_id: "studio" });
  });

  it("自定义补充 ≤200：超长输入被截断到 200，提交体 extra_prompt 长度封顶 200", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/model-studio.png")];
    render(<EcomImageModelForm />);

    fireEvent.change(document.querySelector("#ecom-model-source")!, { target: { files: [png("p.png")] } });
    fireEvent.click(screen.getByRole("button", { name: "简约棚拍" }));
    fireEvent.change(screen.getByLabelText(/自定义补充/), { target: { value: "x".repeat(250) } });
    // footer 计数封顶 200/200
    expect(screen.getByText("200/200")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => expect(modelMock.mutateAsync).toHaveBeenCalled());
    expect(modelMock.mutateAsync.mock.calls[0][0].extra_prompt).toHaveLength(200);
  });

  it("批量：多图 + 选风格 → 生成 → modelBatch fan-out(items×N)+ trackExisting×N + 批量下载", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/1.png"), doneTask("t-2", "https://mock.local/2.png")];
    render(<EcomImageModelForm />);

    fireEvent.click(screen.getByRole("button", { name: "批量" }));
    fireEvent.change(document.querySelector("#ecom-model-batch")!, { target: { files: [png("a.png"), png("b.png")] } });
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "简约棚拍" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => expect(modelBatchMock.mutateAsync).toHaveBeenCalledTimes(1));
    expect(modelBatchMock.mutateAsync.mock.calls[0][0]).toEqual({
      items: [
        { source_asset_id: "asset-1", gender: "female", style_id: "studio", extra_prompt: undefined, apply_visible_label: false },
        { source_asset_id: "asset-1", gender: "female", style_id: "studio", extra_prompt: undefined, apply_visible_label: false }
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
    render(<EcomImageModelForm />);

    fireEvent.click(screen.getByRole("button", { name: "批量" }));
    fireEvent.change(document.querySelector("#ecom-model-batch")!, { target: { files: [png("a.png"), png("b.png")] } });
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "简约棚拍" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => expect(modelBatchMock.mutateAsync).toHaveBeenCalled());

    expect(await screen.findByRole("img", { name: copy.workbench.ecomResultsLabel })).toHaveAttribute("src", "https://mock.local/1.png");
    expect(screen.getByText(copy.errors.imageModeration)).toBeInTheDocument();
  });

  it("风格加载中：出加载态，生成禁用", () => {
    stylesMock.data = [];
    stylesMock.isLoading = true;
    render(<EcomImageModelForm />);
    expect(screen.getByText(copy.workbench.ecomStyleLoading)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成" })).toBeDisabled();
  });

  it("风格加载失败：出风格错误态，生成禁用", () => {
    stylesMock.data = [];
    stylesMock.isError = true;
    render(<EcomImageModelForm />);
    expect(screen.getByText(copy.workbench.ecomStyleError)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成" })).toBeDisabled();
  });

  it("合规提示：模特图为 AI 生成", () => {
    render(<EcomImageModelForm />);
    expect(screen.getByText(copy.workbench.ecomModelCompliance)).toBeInTheDocument();
  });

  it("防连点：提交中按钮显「生成中…」并禁用", () => {
    modelMock.isPending = true;
    render(<EcomImageModelForm />);
    expect(screen.getByRole("button", { name: "生成中…" })).toBeDisabled();
  });
});
