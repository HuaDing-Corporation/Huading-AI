import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@/lib/billing/test-utils";
import { server } from "@/mocks/server";
import { QuotaBadge } from "@/components/layout/quota-badge";
import { EcomImageCutoutForm } from "./ecom-image-cutout-form";
import { EcomImageModelForm } from "./ecom-image-model-form";

const trackExisting = vi.hoisted(() => vi.fn());
vi.mock("@/lib/auth/auth-context", () => ({ useAuth: () => ({ session: { userId: "fixture-user" } }) }));
vi.mock("@/lib/api/hooks", async (loadOriginal) => ({
  ...(await loadOriginal<typeof import("@/lib/api/hooks")>()),
  useUploadImage: () => ({ mutateAsync: async () => ({ asset_id: "source-1" }), isPending: false }),
  useModelStyles: () => ({ data: [], isLoading: false, isError: false })
}));
vi.mock("@/lib/videos/tasks-context", () => ({ useVideoTasks: () => ({ tasks: [], trackExisting }) }));

beforeEach(() => {
  localStorage.clear();
  trackExisting.mockClear();
  URL.createObjectURL = vi.fn(() => "blob:fixture");
  URL.revokeObjectURL = vi.fn();
});

// Real form -> transport -> strict billing parser -> hook -> shared quota query.
// Two bounded cycles use real timers/MSW (not a live backend or a paid request).
describe("accepted ecom task recovery", { timeout: 15_000 }, () => {
  it.each(["cutout", "model"] as const)("%s: persistent 500 allows close/continue, never re-POSTs, then refreshes the real quota", async (kind) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: 0 } } });
    let quotaReads = 0;
    let posted = 0;
    let key = "";
    const lookedUp: string[] = [];
    let recover = false;
    const base = "http://localhost:8000/api/v1";
    server.use(
      http.get(`${base}/quota`, () => {
        quotaReads++;
        return HttpResponse.json({ data: { total: 1000, used: recover ? 269 : 100, remaining: recover ? 731 : 900, reserved: 0, has_active_subscription: true, active_subscription_id: "sub-1", manual_fulfillment_held_credits: 0, pending_refund_credits: 0 }, error: null, request_id: "quota" });
      }),
      http.post(`${base}/ecom-images/${kind}/estimate`, () => HttpResponse.json({ data: {
        pricing_contract: "billing_quote", operation: `ecom_${kind}`, pricing_shape: "simple", unit: "image", quantity: "1", unit_credits: "80", subtotal_credits: "80", payable_credits: 80,
        rate_scope: "tenant_overridable", rate_source: "platform_rate", breakdown: [], disclosures: [], quote_token: "fixture-quote", expires_at: new Date(Date.now() + 60_000).toISOString()
      }, error: null, request_id: "quote" })),
      http.post(`${base}/ecom-images/${kind}`, ({ request }) => {
        posted++;
        key = request.headers.get("Idempotency-Key")!;
        return HttpResponse.json({ data: { task_id: "task-1", status: "queued" }, error: null, request_id: "accepted" }, { status: 202 });
      }),
      http.get(`${base}/billing/operations/by-idempotency/ecom_${kind}/:key`, ({ params }) => {
        lookedUp.push(String(params.key));
        if (!recover) return HttpResponse.json({ data: null, error: { code: "BILLING_INVARIANT_VIOLATION", message: "unavailable" }, request_id: "error" }, { status: 500 });
        const result = { items: [{ item_index: 0, task_id: "task-1", source_asset_id: "source-1", status: "done", asset_id: "output-1" }] };
        return HttpResponse.json({ data: { operation: `ecom_${kind}`, idempotency_key: params.key, state: "completed", completion_kind: "succeeded", billing: { operation_id: "op-1", idempotency_key: params.key, status: "settled", requested_credits: 80, held_credits: 0, settled_credits: 80, released_credits: 0 }, result_type: "ecom_image_batch", result_id: "55555555-5555-4555-8555-555555555555", resource: result, result, failure: null }, error: null, request_id: "completed" });
      })
    );
    render(<><QuotaBadge />{kind === "cutout" ? <EcomImageCutoutForm /> : <EcomImageModelForm />}</>, { wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> });
    expect(await screen.findByText("当前余额 900/1000")).toBeVisible();
    const input = document.querySelector(kind === "cutout" ? "#ecom-cutout-source" : "#ecom-model-product")!;
    fireEvent.change(input, { target: { files: [new File(["image"], "fixture.png", { type: "image/png" })] } });
    await waitFor(() => expect(screen.getByRole("button", { name: "生成" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "生成" }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "确认并继续" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "确认并继续" }));
    // Accepted task is queryable independently of an unavailable billing endpoint.
    await waitFor(() => expect(trackExisting).toHaveBeenCalledWith("task-1", expect.any(String), "photo", false));
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "继续查询" })).toBeEnabled(), { timeout: 5_000 });
    expect(lookedUp).toHaveLength(3);
    expect(within(dialog).getByRole("button", { name: "确认并继续" })).toBeDisabled();
    expect(dialog.textContent).not.toMatch(/已结算|未扣款|生成失败/);
    fireEvent.click(within(dialog).getByRole("button", { name: "关闭" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "结果待确认" })).toBeDisabled();
    // Changing input after close must not discard the sent request or create a new key.
    if (kind === "cutout") fireEvent.click(screen.getByRole("button", { name: "透明底" }));
    else fireEvent.change(screen.getByRole("textbox", { name: "自定义风格" }), { target: { value: "new input" } });
    expect(screen.getByRole("button", { name: "结果待确认" })).toBeDisabled();
    expect(screen.getByText(/结果尚未确认/)).toBeVisible();
    recover = true;
    fireEvent.click(screen.getByRole("button", { name: "继续查询" }));
    expect(await screen.findByText("已结算 80 积分")).toBeVisible();
    expect(await screen.findByText("当前余额 731/1000")).toBeVisible();
    await act(async () => {});
    expect(posted).toBe(1);
    expect(lookedUp).toEqual([key, key, key, key]);
    expect(trackExisting).toHaveBeenCalledTimes(1);
    expect(quotaReads).toBe(2);
    client.clear();
  });
});
