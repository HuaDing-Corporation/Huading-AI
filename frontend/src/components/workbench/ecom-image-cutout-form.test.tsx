import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type {
  BillingOperationLookupFor,
  BillingQuote,
  CutoutBatchRequest,
  CutoutRequest
} from "@/lib/api/types";

const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const estimateCutoutMock = vi.hoisted(() => vi.fn());
const createCutoutMock = vi.hoisted(() => vi.fn());
const lookupBillingMock = vi.hoisted(() => vi.fn());
const trackExistingMock = vi.hoisted(() => vi.fn());
const tasksMock = vi.hoisted(() => ({ tasks: [] as Array<Record<string, unknown>> }));

vi.mock("@/lib/api/hooks", () => ({
  useUploadImage: () => ({ mutateAsync: uploadMock.mutateAsync, isPending: uploadMock.isPending })
}));
vi.mock("@/lib/api/ecom-images", () => ({
  estimateEcomCutout: estimateCutoutMock,
  createEcomCutout: createCutoutMock
}));
vi.mock("@/lib/api/billing", async (loadOriginal) => ({
  ...(await loadOriginal<typeof import("@/lib/api/billing")>()),
  getBillingOperation: lookupBillingMock
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

const quoteFor = (quantity: number): BillingQuote => ({
  pricing_contract: "billing_quote",
  pricing_shape: "simple",
  operation: "ecom_cutout",
  unit: "image",
  quantity: String(quantity),
  unit_credits: "80",
  subtotal_credits: String(quantity * 80),
  payable_credits: quantity * 80,
  rate_scope: "tenant_overridable",
  rate_source: "tenant_rate",
  breakdown: [],
  disclosures: [],
  quote_token: `cutout-quote-${quantity}`,
  expires_at: new Date(Date.now() + 60_000).toISOString()
});

let recoveredTaskIds: string[] = [];

const completedLookup = (
  idempotencyKey: string
): BillingOperationLookupFor<"ecom_cutout"> => {
  const result = {
    items: recoveredTaskIds.map((taskId, itemIndex) => ({
      item_index: itemIndex,
      task_id: taskId,
      source_asset_id: "asset-1",
      status: "done" as const,
      asset_id: `output-${itemIndex}`
    }))
  };
  return {
    operation: "ecom_cutout",
    idempotency_key: idempotencyKey,
    state: "completed",
    completion_kind: "succeeded",
    billing: {
      operation_id: "cutout-operation",
      idempotency_key: idempotencyKey,
      status: "settled",
      requested_credits: recoveredTaskIds.length * 80,
      held_credits: 0,
      settled_credits: recoveredTaskIds.length * 80,
      released_credits: 0
    },
    result_type: "ecom_image_batch",
    result_id: "55555555-5555-4555-8555-555555555555",
    resource: result,
    result,
    failure: null
  };
};

async function confirmGeneration(): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: "生成" }));
  const confirm = await screen.findByRole("button", { name: "确认并继续" });
  await waitFor(() => expect(confirm).toBeEnabled());
  fireEvent.click(confirm);
}

const lastSubmittedBody = (): CutoutRequest | CutoutBatchRequest =>
  createCutoutMock.mock.calls[0][0] as CutoutRequest | CutoutBatchRequest;

beforeEach(() => {
  window.localStorage.clear(); // 每用例干净起点：AI 标识开关默认关，避免记忆跨用例污染
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  uploadMock.isPending = false;
  tasksMock.tasks = [];
  recoveredTaskIds = [];
  uploadMock.mutateAsync.mockReset().mockResolvedValue({ asset_id: "asset-1" });
  estimateCutoutMock.mockReset().mockImplementation((body: CutoutRequest | CutoutBatchRequest) =>
    Promise.resolve(quoteFor("items" in body ? body.items.length : 1))
  );
  createCutoutMock.mockReset().mockImplementation((body: CutoutRequest | CutoutBatchRequest) => {
    if ("items" in body) {
      recoveredTaskIds = body.items.map((_, index) => `t-${index + 1}`);
      return Promise.resolve({
        batch_id: "b-1",
        tasks: body.items.map((item, index) => ({
          task_id: recoveredTaskIds[index],
          source_asset_id: item.source_asset_id,
          status: "queued"
        }))
      });
    }
    recoveredTaskIds = ["t-1"];
    return Promise.resolve({ task_id: "t-1", status: "queued" });
  });
  lookupBillingMock.mockReset().mockImplementation((operation: string, idempotencyKey: string) => {
    expect(operation).toBe("ecom_cutout");
    return Promise.resolve(completedLookup(idempotencyKey));
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

    await confirmGeneration();
    await waitFor(() =>
      expect(createCutoutMock).toHaveBeenCalledTimes(1)
    );
    expect(lastSubmittedBody()).toEqual({ source_asset_id: "asset-1", background: "white", aspect_ratio: "1:1", apply_visible_label: false });
    expect(trackExistingMock).toHaveBeenCalledWith("t-1", expect.any(String), "photo", false);
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
    await confirmGeneration();
    await waitFor(() =>
      expect(createCutoutMock).toHaveBeenCalledTimes(1)
    );
    expect(lastSubmittedBody()).toEqual({ source_asset_id: "asset-1", background: "transparent", aspect_ratio: "1:1", apply_visible_label: false });
  });

  it("批量：多图上传 → 生成 → cutoutBatch fan-out(items×N)+ trackExisting×N + 批量下载", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/1.png"), doneTask("t-2", "https://mock.local/2.png")];
    render(<EcomImageCutoutForm />);

    fireEvent.click(screen.getByRole("button", { name: "批量" }));
    fireEvent.change(document.querySelector("#ecom-cutout-batch")!, {
      target: { files: [png("a.png"), png("b.png")] }
    });
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(2));

    await confirmGeneration();
    await waitFor(() => expect(createCutoutMock).toHaveBeenCalledTimes(1));
    expect(lastSubmittedBody()).toEqual({
      items: [
        { source_asset_id: "asset-1", background: "white", aspect_ratio: "1:1", apply_visible_label: false },
        { source_asset_id: "asset-1", background: "white", aspect_ratio: "1:1", apply_visible_label: false }
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
    await confirmGeneration();
    await waitFor(() => expect(createCutoutMock).toHaveBeenCalled());

    // done 出图(t-1)
    expect(await screen.findByRole("img", { name: copy.workbench.ecomResultsLabel })).toHaveAttribute(
      "src",
      "https://mock.local/1.png"
    );
    // failed 出友好错误(t-2，friendlyImageError 映射 IMAGE_MODERATION_BLOCKED → 区分性文案)
    expect(screen.getByText(copy.errors.imageModeration)).toBeInTheDocument();
  });

  it("防连点：报价中按钮显「生成中…」并禁用", async () => {
    estimateCutoutMock.mockImplementation(() => new Promise<BillingQuote>(() => {}));
    render(<EcomImageCutoutForm />);
    fireEvent.change(document.querySelector("#ecom-cutout-source")!, { target: { files: [png("p.png")] } });
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "生成中…", hidden: true })).toBeDisabled()
    );
  });

  it("批量下载防连点：快速连点 2 次仍只触发一批下载（FIX1 P2-2）", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/1.png"), doneTask("t-2", "https://mock.local/2.png")];
    render(<EcomImageCutoutForm />);
    fireEvent.click(screen.getByRole("button", { name: "批量" }));
    fireEvent.change(document.querySelector("#ecom-cutout-batch")!, {
      target: { files: [png("a.png"), png("b.png")] }
    });
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(2));
    await confirmGeneration();
    await waitFor(() => expect(createCutoutMock).toHaveBeenCalled());

    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const btn = await screen.findByRole("button", { name: /批量下载/ });
    fireEvent.click(btn);
    fireEvent.click(btn); // 第二次（防连点）应被 ref 锁拦截
    // 2 个 done → 仅 1 批 = 2 次下载，而非 2 批 4 次。
    expect(clickSpy).toHaveBeenCalledTimes(2);
    expect(btn).toBeDisabled(); // 下载中视觉反馈
    clickSpy.mockRestore();
  });

  // LABEL-TOGGLE-UI-0001 承重：开启开关 → 电商图提交体带 apply_visible_label:true（锁外壳 applyLabel→闭包→trackExisting 全链路）
  it("开启 AI 标识开关 → 单张 cutout 提交 apply_visible_label:true + trackExisting 第4参 true（承重）", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/cut.png")];
    render(<EcomImageCutoutForm />);
    fireEvent.change(document.querySelector("#ecom-cutout-source")!, { target: { files: [png("p.png")] } });
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());
    fireEvent.click(screen.getByRole("switch")); // 开启 AI 生成标识
    await confirmGeneration();
    await waitFor(() =>
      expect(createCutoutMock).toHaveBeenCalledTimes(1)
    );
    expect(lastSubmittedBody()).toEqual({ source_asset_id: "asset-1", background: "white", aspect_ratio: "1:1", apply_visible_label: true });
    expect(trackExistingMock).toHaveBeenCalledWith("t-1", expect.any(String), "photo", true);
  });

  it("开启 AI 标识开关 → 批量 cutout items 每项 apply_visible_label:true（承重，锁外壳批量链路）", async () => {
    tasksMock.tasks = [doneTask("t-1", "https://mock.local/1.png"), doneTask("t-2", "https://mock.local/2.png")];
    render(<EcomImageCutoutForm />);
    fireEvent.click(screen.getByRole("button", { name: "批量" }));
    fireEvent.change(document.querySelector("#ecom-cutout-batch")!, { target: { files: [png("a.png"), png("b.png")] } });
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("switch")); // 开启 AI 生成标识
    await confirmGeneration();
    await waitFor(() => expect(createCutoutMock).toHaveBeenCalledTimes(1));
    const submitted = lastSubmittedBody();
    expect("items" in submitted ? submitted.items : []).toEqual([
      { source_asset_id: "asset-1", background: "white", aspect_ratio: "1:1", apply_visible_label: true },
      { source_asset_id: "asset-1", background: "white", aspect_ratio: "1:1", apply_visible_label: true }
    ]);
  });
});
