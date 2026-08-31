import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/mocks/server";

const upload = vi.hoisted(() => vi.fn());
const trackExisting = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/hooks", () => ({
  useUploadImage: () => ({ mutateAsync: upload, isPending: false }),
  useCutoutImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCutoutBatch: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useModelImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useModelStyles: () => ({ data: [], isLoading: false, isError: false })
}));
vi.mock("@/lib/videos/tasks-context", () => ({
  useVideoTasks: () => ({ tasks: [], trackExisting })
}));

import { EcomImageCutoutForm } from "./ecom-image-cutout-form";
import { EcomImageModelForm } from "./ecom-image-model-form";

function files(count: number): File[] {
  return Array.from(
    { length: count },
    (_, index) => new File([`image-${index}`], `product-${index}.png`, { type: "image/png" })
  );
}

beforeEach(() => {
  localStorage.clear();
  trackExisting.mockReset();
  upload.mockReset();
  upload.mockImplementation((file: File) =>
    Promise.resolve({ asset_id: `asset-${file.name}` })
  );
  URL.createObjectURL = vi.fn((file: File) => `blob:${file.name}`);
  URL.revokeObjectURL = vi.fn();
});

describe("e-commerce image authoritative batch pricing", () => {
  it("shows the server 80 × N quote, confirms the exact batch, and recovers by the original key", async () => {
    const estimateBodies: unknown[] = [];
    const submitBodies: unknown[] = [];
    let submittedKey = "";
    server.use(
      http.post("http://localhost:8000/api/v1/ecom-images/cutout/estimate", async ({ request }) => {
        estimateBodies.push(await request.json());
        return HttpResponse.json({
          data: {
            pricing_contract: "billing_quote",
            pricing_shape: "simple",
            operation: "ecom_cutout",
            unit: "image",
            quantity: "2",
            unit_credits: "80",
            subtotal_credits: "160",
            payable_credits: 160,
            rate_scope: "tenant_overridable",
            rate_source: "platform_rate",
            breakdown: [],
            disclosures: [],
            quote_token: "cutout-quote",
            expires_at: new Date(Date.now() + 60_000).toISOString()
          },
          error: null,
          request_id: "estimate"
        });
      }),
      http.post("http://localhost:8000/api/v1/ecom-images/cutout/batch", async ({ request }) => {
        submittedKey = request.headers.get("Idempotency-Key") ?? "";
        submitBodies.push({
          body: await request.json(),
          quote: request.headers.get("X-Huading-Quote"),
          key: submittedKey
        });
        return HttpResponse.json({
          data: {
            batch_id: "accepted-batch",
            tasks: [
              { task_id: "task-0", source_asset_id: "asset-product-0.png", status: "queued" },
              { task_id: "task-1", source_asset_id: "asset-product-1.png", status: "queued" }
            ]
          },
          error: null,
          request_id: "submit"
        });
      }),
      http.get("http://localhost:8000/api/v1/billing/operations/by-idempotency/ecom_cutout/:key", ({ params }) => {
        const key = String(params.key);
        const billing = {
          operation_id: "cutout-operation",
          idempotency_key: key,
          status: "partially_settled",
          requested_credits: 160,
          held_credits: 0,
          settled_credits: 80,
          released_credits: 80
        };
        const result = {
          items: [
            { item_index: 0, task_id: "task-0", source_asset_id: "asset-product-0.png", status: "done", asset_id: "output-0" },
            { item_index: 1, task_id: "task-1", source_asset_id: "asset-product-1.png", status: "failed", asset_id: null }
          ]
        };
        return HttpResponse.json({
          data: {
            operation: "ecom_cutout",
            idempotency_key: key,
            state: "completed",
            completion_kind: "succeeded",
            billing,
            result_type: "ecom_image_batch",
            result_id: "55555555-5555-4555-8555-555555555555",
            resource: result,
            result,
            failure: null
          },
          error: null,
          request_id: "lookup"
        });
      })
    );

    render(<EcomImageCutoutForm />);
    fireEvent.click(screen.getByRole("button", { name: "批量" }));
    fireEvent.change(document.querySelector("#ecom-cutout-batch") as HTMLInputElement, {
      target: { files: files(2) }
    });
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "生成" }));

    expect(await screen.findByText("2 image")).toBeVisible();
    expect(screen.getByText("80 积分 / image")).toBeVisible();
    expect(screen.getByText("160 积分", { selector: ".text-2xl" })).toBeVisible();
    expect(submitBodies).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "确认并继续" }));

    await waitFor(() => expect(trackExisting).toHaveBeenCalledTimes(2));
    expect(screen.getByText("部分结算 80 积分，已释放 80 积分")).toBeVisible();
    expect(screen.getByText("仅结算成功生成的图片，失败图片对应的冻结积分已释放。")).toBeVisible();
    expect(submittedKey).toMatch(/^[0-9a-f-]{36}$/i);
    expect(estimateBodies).toEqual([
      {
        items: [
          { source_asset_id: "asset-product-0.png", background: "white", aspect_ratio: "1:1", apply_visible_label: false },
          { source_asset_id: "asset-product-1.png", background: "white", aspect_ratio: "1:1", apply_visible_label: false }
        ]
      }
    ]);
    expect(submitBodies).toEqual([
      { body: estimateBodies[0], quote: "cutout-quote", key: submittedKey }
    ]);
  });

  it("rejects a 21-file selection as one batch and never truncates or uploads it", async () => {
    render(<EcomImageCutoutForm />);
    fireEvent.click(screen.getByRole("button", { name: "批量" }));
    fireEvent.change(document.querySelector("#ecom-cutout-batch") as HTMLInputElement, {
      target: { files: files(21) }
    });

    expect(await screen.findByText("每批最多 20 张图片")).toBeVisible();
    await waitFor(() => expect(upload).not.toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "生成" })).toBeDisabled();
  });

  it("keeps confirmation disabled and never submits when the estimate fails", async () => {
    const submitCalls = vi.fn();
    server.use(
      http.post("http://localhost:8000/api/v1/ecom-images/cutout/estimate", () =>
        HttpResponse.json({
          data: null,
          error: {
            code: "PRICING_UNAVAILABLE",
            message: "报价服务暂不可用",
            detail: null
          },
          request_id: "estimate-failed"
        }, { status: 503 })
      ),
      http.post("http://localhost:8000/api/v1/ecom-images/cutout", () => {
        submitCalls();
        return HttpResponse.json({
          data: { task_id: "must-not-submit", status: "queued" },
          error: null,
          request_id: "unexpected-submit"
        });
      })
    );

    render(<EcomImageCutoutForm />);
    fireEvent.change(document.querySelector("#ecom-cutout-source") as HTMLInputElement, {
      target: { files: [new File(["product"], "product.png", { type: "image/png" })] }
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "生成" }));

    expect(await screen.findByText("暂时无法获取价格，请稍后重试")).toBeVisible();
    const confirm = screen.getByRole("button", { name: "确认并继续" });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(submitCalls).not.toHaveBeenCalled();
  });
});

describe("e-commerce model authoritative pricing", () => {
  it("quotes and confirms the exact model request before creating the task", async () => {
    const bodies: unknown[] = [];
    let key = "";
    server.use(
      http.post("http://localhost:8000/api/v1/ecom-images/model/estimate", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({
          data: {
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
          },
          error: null,
          request_id: "estimate"
        });
      }),
      http.post("http://localhost:8000/api/v1/ecom-images/model", async ({ request }) => {
        key = request.headers.get("Idempotency-Key") ?? "";
        bodies.push({
          body: await request.json(),
          quote: request.headers.get("X-Huading-Quote"),
          key
        });
        return HttpResponse.json({
          data: { task_id: "model-task", status: "queued" },
          error: null,
          request_id: "submit"
        });
      }),
      http.get("http://localhost:8000/api/v1/billing/operations/by-idempotency/ecom_model/:key", ({ params }) => {
        const idempotencyKey = String(params.key);
        const billing = {
          operation_id: "model-operation",
          idempotency_key: idempotencyKey,
          status: "settled",
          requested_credits: 83,
          held_credits: 0,
          settled_credits: 83,
          released_credits: 0
        };
        const result = {
          items: [
            { item_index: 0, task_id: "model-task", source_asset_id: "asset-product.png", status: "done", asset_id: "model-output" }
          ]
        };
        return HttpResponse.json({
          data: {
            operation: "ecom_model",
            idempotency_key: idempotencyKey,
            state: "completed",
            completion_kind: "succeeded",
            billing,
            result_type: "ecom_image_batch",
            result_id: "66666666-6666-4666-8666-666666666666",
            resource: result,
            result,
            failure: null
          },
          error: null,
          request_id: "lookup"
        });
      })
    );

    render(<EcomImageModelForm />);
    fireEvent.change(document.querySelector("#ecom-model-product") as HTMLInputElement, {
      target: { files: [new File(["product"], "product.png", { type: "image/png" })] }
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "生成" }));

    expect(await screen.findByText("83 积分", { selector: ".text-2xl" })).toBeVisible();
    expect(bodies).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "确认并继续" }));
    await waitFor(() => expect(trackExisting).toHaveBeenCalledWith("model-task", expect.any(String), "photo", false));
    expect(bodies).toEqual([
      {
        product_asset_ids: ["asset-product.png"],
        product_images_mode: "multi_item",
        gender: "female",
        aspect_ratio: "1:1",
        apply_visible_label: false
      },
      { body: bodies[0], quote: "model-quote", key }
    ]);
  });

  it("submits only once when confirmation is rapidly double-clicked", async () => {
    const submitCalls = vi.fn();
    server.use(
      http.post("http://localhost:8000/api/v1/ecom-images/model/estimate", () =>
        HttpResponse.json({
          data: {
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
            quote_token: "double-click-model-quote",
            expires_at: new Date(Date.now() + 60_000).toISOString()
          },
          error: null,
          request_id: "double-click-estimate"
        })
      ),
      http.post("http://localhost:8000/api/v1/ecom-images/model", async ({ request }) => {
        submitCalls({
          body: await request.json(),
          quote: request.headers.get("X-Huading-Quote"),
          key: request.headers.get("Idempotency-Key")
        });
        return HttpResponse.json({
          data: { task_id: "double-click-model-task", status: "queued" },
          error: null,
          request_id: "double-click-submit"
        });
      }),
      http.get("http://localhost:8000/api/v1/billing/operations/by-idempotency/ecom_model/:key", ({ params }) => {
        const idempotencyKey = String(params.key);
        const billing = {
          operation_id: "double-click-model-operation",
          idempotency_key: idempotencyKey,
          status: "settled",
          requested_credits: 83,
          held_credits: 0,
          settled_credits: 83,
          released_credits: 0
        };
        const result = {
          items: [{
            item_index: 0,
            task_id: "double-click-model-task",
            source_asset_id: "asset-product.png",
            status: "done",
            asset_id: "double-click-model-output"
          }]
        };
        return HttpResponse.json({
          data: {
            operation: "ecom_model",
            idempotency_key: idempotencyKey,
            state: "completed",
            completion_kind: "succeeded",
            billing,
            result_type: "ecom_image_batch",
            result_id: "77777777-7777-4777-8777-777777777777",
            resource: result,
            result,
            failure: null
          },
          error: null,
          request_id: "double-click-lookup"
        });
      })
    );

    render(<EcomImageModelForm />);
    fireEvent.change(document.querySelector("#ecom-model-product") as HTMLInputElement, {
      target: { files: [new File(["product"], "product.png", { type: "image/png" })] }
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "生成" }));

    expect(await screen.findByText("83 积分", { selector: ".text-2xl" })).toBeVisible();
    const confirm = screen.getByRole("button", { name: "确认并继续" });
    act(() => {
      confirm.click();
      confirm.click();
    });

    await waitFor(() => expect(submitCalls).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(trackExisting).toHaveBeenCalledWith(
        "double-click-model-task",
        expect.any(String),
        "photo",
        false
      )
    );
    expect(submitCalls).toHaveBeenCalledWith({
      body: {
        product_asset_ids: ["asset-product.png"],
        product_images_mode: "multi_item",
        gender: "female",
        aspect_ratio: "1:1",
        apply_visible_label: false
      },
      quote: "double-click-model-quote",
      key: expect.stringMatching(/^[0-9a-f-]{36}$/i)
    });
  });
});
