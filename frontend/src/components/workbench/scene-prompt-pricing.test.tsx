import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/mocks/server";

vi.mock("@/lib/api/hooks", () => ({
  useScriptGenerate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useScenePromptGenerate: () => ({
    mutateAsync: vi.fn().mockResolvedValue({
      scene_prompt: "旧的直接生成路径",
      negative_prompt: "旧负面词"
    }),
    isPending: false
  }),
  useUploadProductImage: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ image_key: "uploads/product-1.png" }),
    isPending: false
  }),
  useUploadImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useEstimateVideo: () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, data: undefined }),
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

import { EcomVideoForm } from "./ecom-video-form";

const API = "http://localhost:8000";

beforeEach(() => {
  localStorage.clear();
  URL.createObjectURL = vi.fn(() => "blob:product");
  URL.revokeObjectURL = vi.fn();
});

describe("scene prompt pricing confirmation", () => {
  it("uses the server's 30-credit quote before generating the bound prompt request", async () => {
    const estimates: unknown[] = [];
    const submits: Array<{ body: unknown; quote: string | null; key: string | null }> = [];
    server.use(
      http.post(`${API}/api/v1/videos/scene-prompt/estimate`, async ({ request }) => {
        estimates.push(await request.json());
        return HttpResponse.json({
          data: {
            pricing_contract: "billing_quote",
            pricing_shape: "simple",
            operation: "scene_prompt",
            unit: "request",
            quantity: "1",
            unit_credits: "30",
            subtotal_credits: "30",
            payable_credits: 30,
            rate_scope: "platform_fixed",
            rate_source: "fixed_policy",
            breakdown: [],
            disclosures: [],
            quote_token: "scene-quote-token",
            expires_at: new Date(Date.now() + 60_000).toISOString()
          },
          error: null,
          request_id: "scene-estimate"
        });
      }),
      http.post(`${API}/api/v1/videos/scene-prompt`, async ({ request }) => {
        const key = request.headers.get("Idempotency-Key");
        submits.push({
          body: await request.json(),
          quote: request.headers.get("X-Huading-Quote"),
          key
        });
        return HttpResponse.json({
          data: {
            scene_prompt: "服务端生成的产品特写",
            negative_prompt: "水印, 变形",
            billing: {
              operation_id: "scene-operation",
              idempotency_key: key,
              status: "settled",
              requested_credits: 30,
              held_credits: 0,
              settled_credits: 30,
              released_credits: 0
            }
          },
          error: null,
          request_id: "scene-submit"
        });
      })
    );

    render(<EcomVideoForm initialTopic="保温杯" />);
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [new File(["image"], "product.png", { type: "image/png" })] }
    });
    const button = screen.getByRole("button", { name: "AI 生成画面" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);

    const dialog = await screen.findByRole("dialog", { name: "确认价格并继续" });
    expect(within(dialog).getAllByText("30 积分").length).toBeGreaterThan(0);
    expect(estimates).toEqual([
      {
        topic: "保温杯",
        product_image_keys: ["uploads/product-1.png"],
        duration_sec: 30
      }
    ]);
    expect(submits).toHaveLength(0);

    fireEvent.click(within(dialog).getByRole("button", { name: "确认并继续" }));

    await waitFor(() => expect(screen.getByDisplayValue("服务端生成的产品特写")).toBeVisible());
    expect(screen.getByDisplayValue("水印, 变形")).toBeVisible();
    expect(submits).toHaveLength(1);
    expect(submits[0]).toMatchObject({
      body: estimates[0],
      quote: "scene-quote-token",
      key: expect.any(String)
    });
  });
});
