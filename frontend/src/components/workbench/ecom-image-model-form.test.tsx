import { fireEvent, screen, waitFor } from "@testing-library/react";
import { render } from "@/lib/billing/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type {
  BillingOperationLookupFor,
  BillingQuote,
  ModelRequest
} from "@/lib/api/types";

const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const estimateModelMock = vi.hoisted(() => vi.fn());
const createModelMock = vi.hoisted(() => vi.fn());
const lookupBillingMock = vi.hoisted(() => vi.fn());
const stylesMock = vi.hoisted(() => ({ data: [] as Array<{ id: string; name: string }>, isLoading: false, isError: false }));
const trackExistingMock = vi.hoisted(() => vi.fn());
const tasksMock = vi.hoisted(() => ({ tasks: [] as Array<Record<string, unknown>> }));

vi.mock("@/lib/api/hooks", () => ({
  useUploadImage: () => ({ mutateAsync: uploadMock.mutateAsync, isPending: uploadMock.isPending }),
  useModelStyles: () => ({ data: stylesMock.data, isLoading: stylesMock.isLoading, isError: stylesMock.isError })
}));
vi.mock("@/lib/api/ecom-images", () => ({
  estimateEcomModel: estimateModelMock,
  createEcomModel: createModelMock
}));
vi.mock("@/lib/api/billing", async (loadOriginal) => ({
  ...(await loadOriginal<typeof import("@/lib/api/billing")>()),
  getBillingOperation: lookupBillingMock
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
// 测试用风格：id 对齐真实预设，name 为中文展示名（getByRole name 精确匹配）
const STYLES = [
  { id: "studio_white", name: "棚拍白底" },
  { id: "street", name: "街拍" }
];

const quoteForModel = (): BillingQuote => ({
  pricing_contract: "billing_quote",
  pricing_shape: "simple",
  operation: "ecom_model",
  unit: "image",
  quantity: "1",
  unit_credits: "83",
  subtotal_credits: "83",
  payable_credits: 83,
  rate_scope: "tenant_overridable",
  rate_source: "tenant_rate",
  breakdown: [],
  disclosures: [],
  quote_token: "model-quote",
  expires_at: new Date(Date.now() + 60_000).toISOString()
});

const completedLookup = (
  idempotencyKey: string
): BillingOperationLookupFor<"ecom_model"> => {
  const result = {
    items: [{
      item_index: 0,
      task_id: "t-1",
      source_asset_id: "asset-1",
      status: "done" as const,
      asset_id: "output-1"
    }]
  };
  return {
    operation: "ecom_model",
    idempotency_key: idempotencyKey,
    state: "completed",
    completion_kind: "succeeded",
    billing: {
      operation_id: "model-operation",
      idempotency_key: idempotencyKey,
      status: "settled",
      requested_credits: 83,
      held_credits: 0,
      settled_credits: 83,
      released_credits: 0
    },
    result_type: "ecom_image_batch",
    result_id: "66666666-6666-4666-8666-666666666666",
    resource: result,
    result,
    failure: null
  };
};

let uploadSeq = 0;
/**
 * 向指定 picker 上传若干图片，等**缩略图渲染落地**——即 setItems→effect→父 onChange(setState) 整条链已 flush，
 * 父组件的 product/model ids 已更新（比只等 mutateAsync 被调用可靠；预览 URL 都是 mock 的 "blob:mock"）。
 */
const uploadTo = async (inputId: string, files: File[]) => {
  const count = () => document.querySelectorAll('img[src="blob:mock"]').length;
  const before = count();
  fireEvent.change(document.querySelector(`#${inputId}`)!, { target: { files } });
  await waitFor(() => expect(count()).toBe(before + files.length));
};
const clickGenerate = () => fireEvent.click(screen.getByRole("button", { name: "生成" }));
async function confirmGeneration(): Promise<void> {
  clickGenerate();
  const confirm = await screen.findByRole("button", { name: "确认并继续" });
  await waitFor(() => expect(confirm).toBeEnabled());
  fireEvent.click(confirm);
}
const lastBody = (): ModelRequest => createModelMock.mock.calls[0][0] as ModelRequest;

beforeEach(() => {
  window.localStorage.clear(); // 每用例干净起点：AI 标识开关默认关
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  uploadSeq = 0;
  uploadMock.isPending = false;
  stylesMock.data = STYLES;
  stylesMock.isLoading = false;
  stylesMock.isError = false;
  tasksMock.tasks = [];
  uploadMock.mutateAsync.mockReset().mockImplementation(async () => ({ asset_id: `asset-${++uploadSeq}` })); // 每次唯一 asset_id
  estimateModelMock.mockReset().mockResolvedValue(quoteForModel());
  createModelMock.mockReset().mockResolvedValue({ task_id: "t-1", status: "queued" });
  lookupBillingMock.mockReset().mockImplementation((operation: string, idempotencyKey: string) => {
    expect(operation).toBe("ecom_model");
    return Promise.resolve(completedLookup(idempotencyKey));
  });
});
afterEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe("EcomImageModelForm (电商图 · AI 模特优化)", () => {
  it("🔴 商品图必填门(D1)：未传商品图 → 生成禁用", () => {
    render(<EcomImageModelForm />);
    expect(screen.getByRole("button", { name: "生成" })).toBeDisabled();
  });

  it("单张 happy path：1 商品图 → 生成 → 提交 product_asset_ids + 默认 multi_item + trackExisting + 结果图", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/model-studio_white.png")];
    render(<EcomImageModelForm />);
    await uploadTo("ecom-model-product", [png("p.png")]);
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());

    await confirmGeneration();
    await waitFor(() => expect(createModelMock).toHaveBeenCalledTimes(1));
    expect(lastBody()).toEqual({
      product_asset_ids: ["asset-1"],
      model_asset_ids: undefined,
      product_images_mode: "multi_item",
      gender: "female",
      style_id: undefined,
      custom_style: undefined,
      extra_prompt: undefined,
      aspect_ratio: "1:1",
      apply_visible_label: false
    });
    expect(trackExistingMock).toHaveBeenCalledWith("t-1", expect.any(String), "photo", false);
    expect(await screen.findByRole("img", { name: copy.workbench.ecomResultsLabel })).toHaveAttribute(
      "src",
      "https://mock.local/model-studio_white.png"
    );
  });

  it("🔴 承重·额度联动(D1)：上传 2 张商品图 → 模特图上限自动变 4 + 剩余额度更新（变异：解除联动 → 红）", async () => {
    render(<EcomImageModelForm />);
    await uploadTo("ecom-model-product", [png("a.png"), png("b.png")]);
    // 模特图 picker 上传按钮 max = 6 − 商品图 2 = 4（联动）
    expect(await screen.findByRole("button", { name: /上传模特图（0\/4）/ })).toBeInTheDocument();
    // 剩余额度：6 − 2 − 0 = 4
    expect(screen.getByText(copy.workbench.ecomModelBudgetRemaining(4))).toBeInTheDocument();
  });

  it("模特图选填(D1)：1 商品图 + 1 模特图 → 提交 model_asset_ids", async () => {
    render(<EcomImageModelForm />);
    await uploadTo("ecom-model-product", [png("p.png")]); // asset-1
    await uploadTo("ecom-model-model", [png("m.png")]); // asset-2
    await confirmGeneration();
    await waitFor(() => expect(createModelMock).toHaveBeenCalled());
    expect(lastBody().product_asset_ids).toEqual(["asset-1"]);
    expect(lastBody().model_asset_ids).toEqual(["asset-2"]);
  });

  it("承重·组合语义默认(D2)：1 商品图（开关隐藏）→ 仍提交 product_images_mode:multi_item", async () => {
    render(<EcomImageModelForm />);
    await uploadTo("ecom-model-product", [png("a.png")]);
    // 单张商品图：组合方式开关不显示（渐进披露）
    expect(screen.queryByRole("group", { name: copy.workbench.ecomProductModeLabel })).not.toBeInTheDocument();
    await confirmGeneration();
    await waitFor(() => expect(createModelMock).toHaveBeenCalled());
    expect(lastBody()).toMatchObject({ product_images_mode: "multi_item" });
  });

  it("🔴 承重·组合语义(D2)：>1 商品图显示开关，选多角度 → 提交 product_images_mode:multi_angle（变异：写死/不传 → 红）", async () => {
    render(<EcomImageModelForm />);
    await uploadTo("ecom-model-product", [png("a.png"), png("b.png")]);
    expect(await screen.findByRole("group", { name: copy.workbench.ecomProductModeLabel })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomProductModeMultiAngle }));
    await confirmGeneration();
    await waitFor(() => expect(createModelMock).toHaveBeenCalled());
    expect(lastBody()).toMatchObject({ product_images_mode: "multi_angle" });
  });

  it("🔴 承重·风格互斥(D3)：选预设 → 自定义框禁用（变异：解除禁用 → 红）", () => {
    render(<EcomImageModelForm />);
    fireEvent.click(screen.getByRole("button", { name: "棚拍白底" }));
    expect(screen.getByLabelText(copy.workbench.ecomCustomStyleLabel)).toBeDisabled();
  });

  it("🔴 承重·风格互斥(D3)：填自定义 → 预设禁用 + 提交带 custom_style 不带 style_id（变异：同送 → 红）", async () => {
    render(<EcomImageModelForm />);
    await uploadTo("ecom-model-product", [png("a.png")]);
    fireEvent.change(screen.getByLabelText(copy.workbench.ecomCustomStyleLabel), { target: { value: "赛博朋克霓虹" } });
    expect(screen.getByRole("button", { name: "棚拍白底" })).toBeDisabled(); // 预设禁用
    await confirmGeneration();
    await waitFor(() => expect(createModelMock).toHaveBeenCalled());
    expect(lastBody().custom_style).toBe("赛博朋克霓虹");
    expect(lastBody().style_id).toBeUndefined();
  });

  it("风格可选(D3)：不选风格、商品图已传 → 生成可用 + 提交无 style_id/custom_style", async () => {
    render(<EcomImageModelForm />);
    await uploadTo("ecom-model-product", [png("a.png")]);
    expect(screen.getByRole("button", { name: "生成" })).toBeEnabled();
    await confirmGeneration();
    await waitFor(() => expect(createModelMock).toHaveBeenCalled());
    expect(lastBody().style_id).toBeUndefined();
    expect(lastBody().custom_style).toBeUndefined();
  });

  it("选风格预设 → 提交带 style_id", async () => {
    render(<EcomImageModelForm />);
    await uploadTo("ecom-model-product", [png("a.png")]);
    fireEvent.click(screen.getByRole("button", { name: "街拍" }));
    await confirmGeneration();
    await waitFor(() => expect(createModelMock).toHaveBeenCalled());
    expect(lastBody().style_id).toBe("street");
  });

  it("🔴 自定义补充取消 200 限制(D4)：输入 250 字 → 无 200 计数、提交 extra_prompt 全长 250（变异：改回 slice(0,200) → 红）", async () => {
    render(<EcomImageModelForm />);
    await uploadTo("ecom-model-product", [png("a.png")]);
    fireEvent.change(screen.getByLabelText(/自定义补充/), { target: { value: "x".repeat(250) } });
    expect(screen.queryByText("200/200")).not.toBeInTheDocument(); // 计数封顶已移除
    await confirmGeneration();
    await waitFor(() => expect(createModelMock).toHaveBeenCalled());
    expect(lastBody().extra_prompt).toHaveLength(250);
  });

  it("性别：选男 → 提交 gender:male", async () => {
    render(<EcomImageModelForm />);
    await uploadTo("ecom-model-product", [png("p.png")]);
    fireEvent.click(screen.getByRole("button", { name: "男" }));
    await confirmGeneration();
    await waitFor(() => expect(createModelMock).toHaveBeenCalled());
    expect(lastBody().gender).toBe("male");
  });

  it("开启 AI 标识开关 → 提交 apply_visible_label:true + trackExisting 第 4 参 true（承重）", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/model-studio_white.png")];
    render(<EcomImageModelForm />);
    await uploadTo("ecom-model-product", [png("p.png")]);
    fireEvent.click(screen.getByRole("switch")); // 开启 AI 生成标识
    await confirmGeneration();
    await waitFor(() => expect(createModelMock).toHaveBeenCalled());
    expect(lastBody().apply_visible_label).toBe(true);
    expect(trackExistingMock).toHaveBeenCalledWith("t-1", expect.any(String), "photo", true);
  });

  it("风格加载中：出加载态；风格可选 → 商品图已传即可生成（不被风格阻断）", async () => {
    stylesMock.data = [];
    stylesMock.isLoading = true;
    render(<EcomImageModelForm />);
    expect(screen.getByText(copy.workbench.ecomStyleLoading)).toBeInTheDocument();
    await uploadTo("ecom-model-product", [png("p.png")]);
    expect(screen.getByRole("button", { name: "生成" })).toBeEnabled();
  });

  it("风格加载失败：出风格错误态（可选，不阻断）", () => {
    stylesMock.data = [];
    stylesMock.isError = true;
    render(<EcomImageModelForm />);
    expect(screen.getByText(copy.workbench.ecomStyleError)).toBeInTheDocument();
  });

  it("合规提示：模特图为 AI 生成", () => {
    render(<EcomImageModelForm />);
    expect(screen.getByText(copy.workbench.ecomModelCompliance)).toBeInTheDocument();
  });

  it("防连点：报价中按钮显「生成中…」并禁用", async () => {
    estimateModelMock.mockImplementation(() => new Promise<BillingQuote>(() => {}));
    render(<EcomImageModelForm />);
    await uploadTo("ecom-model-product", [png("p.png")]);
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());
    clickGenerate();
    await waitFor(() =>
      expect(screen.getByText("生成中…", { selector: "button" })).toBeDisabled()
    );
  });
});
