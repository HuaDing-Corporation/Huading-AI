import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { ConfirmGenerateDialog } from "./confirm-generate-dialog";
import { useGenerateConfirm } from "@/lib/api/use-generate-confirm";
import { createVideo } from "@/lib/api/videos";
import type { CreateVideoRequest } from "@/lib/api/types";
import { avatarPolicy } from "@/lib/api/testing/avatar-policy";
import { server } from "@/mocks/server";

const API = "http://localhost:8000/api/v1";
function Harness({ request }: { request: CreateVideoRequest }) {
  const control = useGenerateConfirm((body) => createVideo(body), { authoritativePricing: false });
  return <>
    <button onClick={() => control.requestConfirm(request)}>打开确认</button>
    <button onClick={() => void control.confirm(true)}>仅传布尔值</button>
    <ConfirmGenerateDialog open={control.open} request={control.request}
      submitting={control.submitting} pricing={control.pricing}
      onConfirm={control.confirm} onCancel={control.cancel} />
  </>;
}

describe.each(["photo", "video"] as const)("disabled authoritative pricing: %s", (source) => {
  const request: CreateVideoRequest = {
    topic: "测试口播", script: "测试文案", voice_id: "voice-1",
    ...(source === "photo" ? { avatar_asset_id: "photo-1" } : { avatar_video_asset_id: "video-1" })
  };
  function setup(mode: "valid" | "missing" | "expired" = "valid") {
    const estimates = vi.fn();
    const submissions = vi.fn();
    server.use(
      http.post(`${API}/videos/estimate`, async ({ request: incoming }) => {
        estimates(await incoming.json());
        return HttpResponse.json({ data: {
          pricing_contract: "legacy_estimate", estimated_credits: 123, unit: "credits",
          ...(mode === "missing" ? {} : { avatar_duration_policy: {
            ...avatarPolicy(123), token: `synthetic-${source}-${estimates.mock.calls.length}`,
            ...(mode === "expired" ? { expires_at: "2020-01-01T00:00:00Z" } : {})
          } })
        }, error: null, request_id: "fallback-estimate" });
      }),
      http.post(`${API}/videos`, async ({ request: incoming }) => {
        submissions({ body: await incoming.json(), quote: incoming.headers.get("X-Huading-Quote"),
          key: incoming.headers.get("Idempotency-Key") });
        return HttpResponse.json({ data: { id: "task-1", status: "queued", pricing_contract: "legacy_estimate" },
          error: null, request_id: "fallback-submit" }, { status: 202 });
      })
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><Harness request={request} /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: "打开确认" }));
    return { estimates, submissions };
  }

  // Break caught: dialog's estimate never reaches the disabled hook's empty cache.
  it("submits the displayed signed policy only after consent, without a second estimate or billing headers", async () => {
    const { estimates, submissions } = setup();
    await screen.findByText("123");
    const confirm = screen.getByRole("button", { name: "确定" });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    await act(async () => {});
    expect(submissions).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(confirm);
    await waitFor(() => expect(submissions).toHaveBeenCalledTimes(1));
    expect(submissions).toHaveBeenCalledWith({ body: { ...request,
      avatar_duration_policy: "145s-no-refund-v1", avatar_duration_policy_token: `synthetic-${source}-1`
    }, quote: null, key: null });
    expect(estimates).toHaveBeenCalledTimes(1);
    expect(estimates).toHaveBeenCalledWith(request);
  });

  it("cancel/reopen requires new consent and submits the replacement policy", async () => {
    const { submissions, estimates } = setup();
    await screen.findByText("123");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    fireEvent.click(screen.getByRole("button", { name: "打开确认" }));
    await screen.findByText("123");
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "确定" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "确定" }));
    await waitFor(() => expect(submissions).toHaveBeenCalledTimes(1));
    expect(submissions.mock.calls[0][0].body.avatar_duration_policy_token).toBe(`synthetic-${source}-2`);
    expect(estimates).toHaveBeenCalledTimes(2);
  });

  it.each(["missing", "expired"] as const)("never submits a %s policy", async (mode) => {
    const { submissions } = setup(mode);
    await screen.findByText("123");
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "确定" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "确定" }));
    await act(async () => {});
    expect(submissions).not.toHaveBeenCalled();
  });

  it("a boolean alone cannot turn an estimate into consent at the hook boundary", async () => {
    const { submissions } = setup();
    await screen.findByText("123");
    fireEvent.click(screen.getByRole("button", { name: "仅传布尔值", hidden: true }));
    await act(async () => {});
    expect(submissions).not.toHaveBeenCalled();
  });
});
