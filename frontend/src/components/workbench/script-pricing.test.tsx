import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { render } from "@/lib/billing/test-utils";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/mocks/server";

vi.mock("@/lib/api/hooks", () => ({
  useScriptGenerate: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ script: "旧的直接生成路径" }),
    isPending: false
  }),
  useUploadImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadAvatarVideo: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadProductImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useBrandVoices: () => ({ data: [], isLoading: false }),
  useVoices: () => ({
    data: [
      {
        id: "voice-1",
        provider: "edge_tts",
        voice_code: "voice",
        display_name: "测试音色",
        gender: null,
        language: null
      }
    ]
  }),
  useAvatarPresets: () => ({ data: [] }),
  useSubtitleTemplates: () => ({ data: [] })
  ,useEstimateVideo: () => ({
    mutate: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    data: undefined
  })
}));
vi.mock("@/lib/videos/tasks-context", () => ({
  useVideoTasks: () => ({ createAndTrack: vi.fn() })
}));
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({
    session: { role: "admin", user: { permissions: ["voice_clone_vip"] } },
    ready: true
  })
}));

import { NewVideoForm } from "./new-video-form";
import { EcomVideoForm } from "./ecom-video-form";

const API = "http://localhost:8000";

function quote() {
  return {
    pricing_contract: "billing_quote",
    pricing_shape: "simple",
    operation: "script_generate",
    unit: "request",
    quantity: "1",
    unit_credits: "1",
    subtotal_credits: "1",
    payable_credits: 1,
    rate_scope: "platform_fixed",
    rate_source: "fixed_policy",
    breakdown: [],
    disclosures: [],
    quote_token: "script-quote-token",
    expires_at: new Date(Date.now() + 60_000).toISOString()
  };
}

beforeEach(() => {
  localStorage.clear();
});

describe("script pricing confirmation", () => {
  it("quotes the exact request and confirms it before generating a script", async () => {
    const estimates: unknown[] = [];
    const submits: Array<{ body: unknown; quote: string | null; key: string | null }> = [];
    server.use(
      http.post(`${API}/api/v1/scripts/estimate`, async ({ request }) => {
        estimates.push(await request.json());
        return HttpResponse.json({ data: quote(), error: null, request_id: "estimate-script" });
      }),
      http.post(`${API}/api/v1/scripts/generate`, async ({ request }) => {
        const key = request.headers.get("Idempotency-Key");
        submits.push({
          body: await request.json(),
          quote: request.headers.get("X-Huading-Quote"),
          key
        });
        return HttpResponse.json({
          data: {
            script: "服务端生成的口播文案",
            billing: {
              operation_id: "script-operation",
              idempotency_key: key,
              status: "settled",
              requested_credits: 1,
              held_credits: 0,
              settled_credits: 1,
              released_credits: 0
            }
          },
          error: null,
          request_id: "submit-script"
        });
      })
    );

    render(<NewVideoForm initialTopic="保温杯" />);
    fireEvent.click(screen.getByRole("button", { name: "重写文案" }));

    const dialog = await screen.findByRole("dialog", { name: "确认价格并继续" });
    expect((await within(dialog).findAllByText("1 积分")).length).toBeGreaterThan(0);
    expect(estimates).toEqual([{ topic: "保温杯" }]);
    expect(submits).toHaveLength(0);

    fireEvent.click(within(dialog).getByRole("button", { name: "确认并继续" }));

    await waitFor(() => expect(screen.getByDisplayValue("服务端生成的口播文案")).toBeVisible());
    expect(submits).toHaveLength(1);
    expect(submits[0]).toMatchObject({
      body: { topic: "保温杯" },
      quote: "script-quote-token",
      key: expect.any(String)
    });
    expect(screen.getByText("已结算 1 积分")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "重写文案" }));
    await waitFor(() => expect(screen.queryByText("已结算 1 积分")).not.toBeInTheDocument());
  });

  it("binds the e-commerce script quote to duration, mode, and length tier", async () => {
    const estimates: unknown[] = [];
    const submits: Array<{ body: unknown; quote: string | null; key: string | null }> = [];
    server.use(
      http.post(`${API}/api/v1/scripts/estimate`, async ({ request }) => {
        estimates.push(await request.json());
        return HttpResponse.json({ data: quote(), error: null, request_id: "estimate-ecom-script" });
      }),
      http.post(`${API}/api/v1/scripts/generate`, async ({ request }) => {
        const key = request.headers.get("Idempotency-Key");
        submits.push({
          body: await request.json(),
          quote: request.headers.get("X-Huading-Quote"),
          key
        });
        return HttpResponse.json({
          data: {
            script: "服务端生成的电商文案",
            billing: {
              operation_id: "ecom-script-operation",
              idempotency_key: key,
              status: "settled",
              requested_credits: 1,
              held_credits: 0,
              settled_credits: 1,
              released_credits: 0
            }
          },
          error: null,
          request_id: "submit-ecom-script"
        });
      })
    );

    render(<EcomVideoForm initialTopic="保温杯" />);
    fireEvent.click(screen.getByRole("button", { name: "AI生成文案" }));

    const dialog = await screen.findByRole("dialog", { name: "确认价格并继续" });
    expect((await within(dialog).findAllByText("1 积分")).length).toBeGreaterThan(0);
    expect(estimates).toEqual([
      {
        topic: "保温杯",
        video_mode: "seedance_i2v",
        duration_sec: 30,
        length_tier: "medium"
      }
    ]);
    expect(submits).toHaveLength(0);

    fireEvent.click(within(dialog).getByRole("button", { name: "确认并继续" }));
    await waitFor(() => expect(screen.getByDisplayValue("服务端生成的电商文案")).toBeVisible());
    expect(submits).toHaveLength(1);
    expect(submits[0]).toMatchObject({
      body: estimates[0],
      quote: "script-quote-token",
      key: expect.any(String)
    });
    expect(screen.getByText("已结算 1 积分")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "AI生成文案" }));
    await waitFor(() => expect(screen.queryByText("已结算 1 积分")).not.toBeInTheDocument());
  });
});
