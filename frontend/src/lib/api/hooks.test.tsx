import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";
import type { ReactNode } from "react";

vi.mock("@/lib/api/videos", () => ({
  listVideos: vi.fn(),
  getVideo: vi.fn(),
  createVideo: vi.fn()
}));
vi.mock("@/lib/api/uploads", () => ({ uploadImage: vi.fn(), uploadProductImage: vi.fn() }));
vi.mock("@/lib/api/auth", () => ({ fetchMe: vi.fn() }));
vi.mock("@/lib/api/scripts", () => ({ generateScript: vi.fn() }));
vi.mock("@/lib/api/voices", () => ({ listVoices: vi.fn().mockResolvedValue([]) }));
vi.mock("@/lib/api/brand-voices", () => ({
  listBrandVoices: vi.fn(),
  deleteBrandVoice: vi.fn()
}));
vi.mock("@/lib/api/brand-voice-orders", () => ({ listBrandVoiceOrders: vi.fn().mockResolvedValue([]) }));
vi.mock("@/lib/api/avatars", () => ({ listAvatarPresets: vi.fn().mockResolvedValue([]) }));
vi.mock("@/lib/api/quota", () => ({ getQuota: vi.fn().mockResolvedValue({ total: 0, used: 0, reserved: 0, remaining: 0 }) }));
vi.mock("@/lib/api/copy", () => ({ estimateCopy: vi.fn() }));
vi.mock("@/lib/api/ecom-images", () => ({
  estimateEcomCutout: vi.fn(),
  createEcomCutout: vi.fn(),
  estimateEcomModel: vi.fn(),
  createEcomModel: vi.fn()
}));

let mockSession: { token: string; tenantId: string } | null = { token: "t", tenantId: "ten-a" };
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: mockSession, ready: true, login: vi.fn(), logout: vi.fn() })
}));

import { listVideos, createVideo } from "@/lib/api/videos";
import { estimateCopy } from "@/lib/api/copy";
import { listBrandVoices } from "@/lib/api/brand-voices";
import {
  createEcomCutout,
  createEcomModel,
  estimateEcomCutout,
  estimateEcomModel
} from "@/lib/api/ecom-images";
import {
  useCreateEcomCutout,
  useCreateEcomModel,
  useEstimateEcomCutout,
  useEstimateEcomModel,
  useVideos,
  useCreateVideo,
  useEstimateCopy,
  useBrandVoices
} from "@/lib/api/hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  mockSession = { token: "t", tenantId: "ten-a" };
});

describe("useBrandVoices polling authority", () => {
  it("does not poll a canonical Doubao manual-delivery processing record", async () => {
    vi.useFakeTimers();
    (listBrandVoices as Mock).mockResolvedValue([
      { id: "doubao-1", provider: "doubao-voice-clone", status: "processing" }
    ]);
    renderHook(() => useBrandVoices(), { wrapper });
    await act(async () => { await Promise.resolve(); });
    expect(listBrandVoices).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(listBrandVoices).toHaveBeenCalledTimes(1);
  });

  it("keeps polling a CosyVoice processing record every three seconds", async () => {
    vi.useFakeTimers();
    (listBrandVoices as Mock).mockResolvedValue([
      { id: "cosy-1", provider: "cosyvoice-voice-clone", status: "processing" }
    ]);
    renderHook(() => useBrandVoices(), { wrapper });
    await act(async () => { await Promise.resolve(); });
    expect(listBrandVoices).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(listBrandVoices).toHaveBeenCalledTimes(2);
  });
});

describe("useVideos", () => {
  it("fetches the list when a session is present", async () => {
    (listVideos as Mock).mockResolvedValue([{ id: "v1", status: "queued", progress: 0, topic: "t", created_at: "" }]);
    const { result } = renderHook(() => useVideos(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([{ id: "v1", status: "queued", progress: 0, topic: "t", created_at: "" }]);
  });

  it("does not fetch when there is no session", async () => {
    mockSession = null;
    (listVideos as Mock).mockResolvedValue([]);
    renderHook(() => useVideos(), { wrapper });
    await new Promise((r) => setTimeout(r, 0));
    expect(listVideos).not.toHaveBeenCalled();
  });
});

describe("useCreateVideo", () => {
  it("creates a video via the mutation", async () => {
    (createVideo as Mock).mockResolvedValue({ id: "x", status: "queued" });
    const { result } = renderHook(() => useCreateVideo(), { wrapper });
    result.current.mutate({ topic: "hi", voice_id: "v", avatar_asset_id: "a" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(createVideo).toHaveBeenCalledWith({ topic: "hi", voice_id: "v", avatar_asset_id: "a" });
  });
});

describe("useEstimateCopy", () => {
  /** 生产变异：estimate query key 不带 tenantId → 切到 B 后仍显示 A 的报价，且不会发第二次请求。 */
  it("切换租户时隔离报价缓存：B 等待新响应期间不得沿用 A 的金额", async () => {
    let resolveTenantB!: (value: {
      estimated_credits: number;
      unit: "credits";
      note: string | null;
      breakdown: Array<{ operation: "rewrite" | "titles" | "topics"; estimated_credits: number }>;
    }) => void;
    (estimateCopy as Mock)
      .mockResolvedValueOnce({
        estimated_credits: 3,
        unit: "credits",
        note: null,
        breakdown: [
          { operation: "rewrite", estimated_credits: 1 },
          { operation: "titles", estimated_credits: 1 },
          { operation: "topics", estimated_credits: 1 }
        ]
      })
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveTenantB = resolve;
          })
      );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const tenantWrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, rerender } = renderHook(() => useEstimateCopy(), { wrapper: tenantWrapper });
    await waitFor(() => expect(result.current.data?.estimated_credits).toBe(3));

    mockSession = { token: "t-b", tenantId: "ten-b" };
    rerender();

    await waitFor(() => expect(estimateCopy).toHaveBeenCalledTimes(2));
    expect(result.current.data).toBeUndefined();

    resolveTenantB({
      estimated_credits: 6,
      unit: "credits",
      note: null,
      breakdown: [
        { operation: "rewrite", estimated_credits: 2 },
        { operation: "titles", estimated_credits: 2 },
        { operation: "topics", estimated_credits: 2 }
      ]
    });
    await waitFor(() => expect(result.current.data?.estimated_credits).toBe(6));
  });
});

describe("authoritative e-commerce billing hooks", () => {
  it("keeps estimate input and confirmed cutout submission explicit", async () => {
    const body = { source_asset_id: "asset-1", background: "white" as const };
    const confirmation = {
      quote_token: "quote",
      idempotency_key: "11111111-1111-4111-8111-111111111111"
    };
    (estimateEcomCutout as Mock).mockResolvedValue({ operation: "ecom_cutout", payable_credits: 80 });
    (createEcomCutout as Mock).mockResolvedValue({ task_id: "task-1", status: "queued" });

    const estimate = renderHook(() => useEstimateEcomCutout(), { wrapper });
    estimate.result.current.mutate(body);
    await waitFor(() => expect(estimate.result.current.isSuccess).toBe(true));
    expect(estimateEcomCutout).toHaveBeenCalledWith(body);

    const create = renderHook(() => useCreateEcomCutout(), { wrapper });
    create.result.current.mutate({ body, confirmation });
    await waitFor(() => expect(create.result.current.isSuccess).toBe(true));
    expect(createEcomCutout).toHaveBeenCalledWith(body, confirmation);
  });

  it("keeps estimate input and confirmed model submission explicit", async () => {
    const body = {
      product_asset_ids: ["asset-1"],
      product_images_mode: "multi_item" as const,
      gender: "female" as const
    };
    const confirmation = {
      quote_token: "model-quote",
      idempotency_key: "22222222-2222-4222-8222-222222222222"
    };
    (estimateEcomModel as Mock).mockResolvedValue({ operation: "ecom_model", payable_credits: 80 });
    (createEcomModel as Mock).mockResolvedValue({ task_id: "model-task", status: "queued" });

    const estimate = renderHook(() => useEstimateEcomModel(), { wrapper });
    estimate.result.current.mutate(body);
    await waitFor(() => expect(estimate.result.current.isSuccess).toBe(true));
    expect(estimateEcomModel).toHaveBeenCalledWith(body);

    const create = renderHook(() => useCreateEcomModel(), { wrapper });
    create.result.current.mutate({ body, confirmation });
    await waitFor(() => expect(create.result.current.isSuccess).toBe(true));
    expect(createEcomModel).toHaveBeenCalledWith(body, confirmation);
  });
});
