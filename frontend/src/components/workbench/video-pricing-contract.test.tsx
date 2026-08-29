import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmGenerateDialog } from "@/components/workbench/confirm-generate-dialog";
import { createVideo } from "@/lib/api/videos";
import type { BillingConfirmation, VideoEstimateContract } from "@/lib/api/types";
import { useGenerateConfirm } from "@/lib/api/use-generate-confirm";
import { server } from "@/mocks/server";

const API = "http://localhost:8000";
const request = {
  topic: "品牌口播",
  script: "四字文案",
  voice_id: "brand-voice",
  avatar_asset_id: "avatar-1"
};

function quote(withTts = true) {
  const lines = [
    {
      operation: "video_create",
      capability: "video",
      unit: "second",
      quantity: "1",
      unit_credits: "100.0000",
      subtotal_credits: "100.0000",
      rate_scope: "tenant_overridable",
      rate_source: "code_default",
      rate_id: null,
      effective_at: null,
      policy_key: "video_create",
      policy_version: 1,
      label: "品牌视频"
    }
  ];
  if (withTts) {
    lines.push({
      operation: "cosyvoice_brand_tts",
      capability: "tts",
      unit: "character",
      quantity: "4",
      unit_credits: "0.1000",
      subtotal_credits: "0.4000",
      rate_scope: "platform_fixed",
      rate_source: "fixed_policy",
      rate_id: null,
      effective_at: null,
      policy_key: "cosyvoice_brand_tts",
      policy_version: 1,
      label: "CosyVoice 品牌音色"
    });
  }
  return {
    pricing_contract: "billing_quote",
    pricing_shape: "composite",
    operation: "video_create",
    unit: null,
    quantity: null,
    unit_credits: null,
    rate_scope: null,
    rate_source: null,
    subtotal_credits: withTts ? "100.4000" : "100.0000",
    payable_credits: withTts ? 101 : 100,
    breakdown: lines,
    disclosures: [],
    quote_token: "signed-video-quote",
    expires_at: "2030-01-01T00:00:00Z"
  };
}

function Harness({ onSubmit }: {
  onSubmit?: (
    estimate: VideoEstimateContract | undefined,
    confirmation: BillingConfirmation | undefined
  ) => void;
}) {
  const control = useGenerateConfirm(async (body, estimate, confirmation) => {
    onSubmit?.(estimate, confirmation);
    return createVideo(body, confirmation);
  }, { authoritativePricing: true });
  return (
    <>
      <button onClick={() => control.requestConfirm(request)}>生成视频</button>
      <ConfirmGenerateDialog
        open={control.open}
        request={control.request}
        submitting={control.submitting}
        pricing={control.pricing}
        onConfirm={() => void control.confirm()}
        onCancel={control.cancel}
      />
    </>
  );
}

function renderHarness(onSubmit?: Parameters<typeof Harness>[0]["onSubmit"]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Harness onSubmit={onSubmit} />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("video pricing contract UI", () => {
  it("fetches a CosyVoice composite quote once and reuses it for the exact confirmed submit", async () => {
    const estimateCalls = vi.fn();
    const submitCalls = vi.fn();
    server.use(
      http.post(`${API}/api/v1/videos/estimate`, async ({ request: incoming }) => {
        estimateCalls(await incoming.json());
        return HttpResponse.json({ data: quote(true), error: null, request_id: "estimate" });
      }),
      http.post(`${API}/api/v1/videos`, async ({ request: incoming }) => {
        const body = await incoming.json();
        const key = incoming.headers.get("Idempotency-Key")!;
        submitCalls({
          body,
          quote: incoming.headers.get("X-Huading-Quote"),
          key
        });
        return HttpResponse.json({
          data: {
            id: "video-1",
            task_id: "video-1",
            status: "queued",
            pricing_contract: "billing_quote",
            billing: {
              operation_id: "operation-1",
              idempotency_key: key,
              status: "reserved",
              requested_credits: 101,
              held_credits: 101,
              settled_credits: 0,
              released_credits: 0
            }
          },
          error: null,
          request_id: "submit"
        }, { status: 202 });
      })
    );

    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: "生成视频" }));
    expect(await screen.findByText("CosyVoice 品牌音色")).toBeVisible();
    expect(screen.getByText("4 character · 0.1000 积分 / character")).toBeVisible();
    expect(screen.getByText("101 积分")).toBeVisible();
    expect(estimateCalls).toHaveBeenCalledTimes(1);

    const confirm = screen.getByRole("button", { name: "确认并继续" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(submitCalls).toHaveBeenCalledTimes(1));
    expect(estimateCalls).toHaveBeenCalledTimes(1);
    expect(submitCalls).toHaveBeenCalledWith({
      body: request,
      quote: "signed-video-quote",
      key: expect.stringMatching(/^[0-9a-f-]{36}$/i)
    });
  });

  it("keeps a Doubao branded quote simple with no invented CosyVoice character line", async () => {
    server.use(
      http.post(`${API}/api/v1/videos/estimate`, () =>
        HttpResponse.json({ data: quote(false), error: null, request_id: "doubao" })
      )
    );
    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: "生成视频" }));
    expect(await screen.findByText("品牌视频")).toBeVisible();
    expect(screen.queryByText(/character/)).not.toBeInTheDocument();
    expect(screen.getByText("100 积分")).toBeVisible();
  });

  it("submits legacy without billing headers after showing the server estimate", async () => {
    const submit = vi.fn();
    server.use(
      http.post(`${API}/api/v1/videos/estimate`, () =>
        HttpResponse.json({
          data: {
            pricing_contract: "legacy_estimate",
            estimated_credits: 12,
            unit: "credits",
            note: "旧计费流程"
          },
          error: null,
          request_id: "legacy-estimate"
        })
      ),
      http.post(`${API}/api/v1/videos`, ({ request: incoming }) => {
        submit({
          quote: incoming.headers.get("X-Huading-Quote"),
          key: incoming.headers.get("Idempotency-Key")
        });
        return HttpResponse.json({
          data: {
            id: "legacy-1",
            task_id: "legacy-1",
            status: "queued",
            pricing_contract: "legacy_estimate"
          },
          error: null,
          request_id: "legacy-submit"
        }, { status: 202 });
      })
    );
    const callback = vi.fn();
    renderHarness(callback);
    fireEvent.click(screen.getByRole("button", { name: "生成视频" }));
    expect(await screen.findByText("12")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确定" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ quote: null, key: null }));
    expect(callback.mock.calls[0][0]).toMatchObject({ pricing_contract: "legacy_estimate" });
    expect(callback.mock.calls[0][1]).toBeUndefined();
  });

  it("labels deferred as unfinished pricing and never presents zero as free", async () => {
    server.use(
      http.post(`${API}/api/v1/videos/estimate`, () =>
        HttpResponse.json({
          data: {
            pricing_contract: "deferred_unpriced",
            estimated_credits: 0,
            unit: "credits",
            unpriced: true,
            note: "等待后续闭环"
          },
          error: null,
          request_id: "deferred"
        })
      ),
      http.post(`${API}/api/v1/videos`, () =>
        HttpResponse.json({
          data: {
            id: "deferred-1",
            task_id: "deferred-1",
            status: "queued",
            pricing_contract: "deferred_unpriced"
          },
          error: null,
          request_id: "deferred-submit"
        }, { status: 202 })
      )
    );
    const callback = vi.fn();
    renderHarness(callback);
    fireEvent.click(screen.getByRole("button", { name: "生成视频" }));
    expect(await screen.findByText("延期处理／尚未闭环")).toBeVisible();
    expect(screen.queryByText(/0\s*积分|免费/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确定" }));
    await waitFor(() => expect(callback).toHaveBeenCalled());
    expect(callback.mock.calls[0][0]).toMatchObject({
      pricing_contract: "deferred_unpriced",
      unpriced: true
    });
    expect(callback.mock.calls[0][1]).toBeUndefined();
  });

  it("disables submit when the authoritative estimate fails", async () => {
    server.use(
      http.post(`${API}/api/v1/videos/estimate`, () =>
        HttpResponse.json({
          data: null,
          error: { code: "BILLABLE_TEXT_REQUIRED", message: "品牌音色视频需要文案。" },
          request_id: "missing-text"
        }, { status: 422 })
      )
    );
    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: "生成视频" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("品牌音色视频需要文案");
    expect(screen.getByRole("button", { name: "确定" })).toBeDisabled();
  });
});
