import { act, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { BillingQuote } from "@/lib/api/types";
import { renderHook } from "./test-utils";
import { useBillingAction } from "./use-billing-action";

const key = "11111111-1111-4111-8111-111111111111";

function quote(): BillingQuote {
  return {
    pricing_contract: "billing_quote", operation: "ecom_cutout",
    pricing_shape: "simple", unit: "image", quantity: "1", unit_credits: "80",
    rate_scope: "tenant_overridable", rate_source: "platform_rate",
    subtotal_credits: "80", payable_credits: 80, breakdown: [], disclosures: [],
    quote_token: "transport-fixture", expires_at: new Date(Date.now() + 60_000).toISOString()
  };
}

// Real hook -> getBillingOperation -> apiFetch. Only the HTTP transport hangs;
// a rejected outer wait must not be confused with an aborted underlying GET.
function harness() {
  let active = 0;
  let peak = 0;
  const signals: (AbortSignal | null | undefined)[] = [];
  const requestPaths: string[] = [];
  const transport = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    active++;
    peak = Math.max(peak, active);
    signals.push(init?.signal);
    requestPaths.push(String(input));
    return new Promise<Response>((_resolve, reject) => {
      const abort = () => { active--; reject(new DOMException("aborted", "AbortError")); };
      if (init?.signal?.aborted) abort();
      else init?.signal?.addEventListener("abort", abort, { once: true });
    });
  });
  vi.stubGlobal("fetch", transport);
  const submit = vi.fn(async () => ({ taskIds: ["transport-task"] }));
  const view = renderHook(() => useBillingAction({
    operation: "ecom_cutout", input: { source_asset_id: "transport-source" },
    estimate: async () => quote(), submit,
    createIdempotencyKey: () => key,
    resultFromLookup: () => null
  }));
  return { view, submit, transport, requestPaths, signals, active: () => active, peak: () => peak };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("billing recovery owns and cancels the real GET transport", () => {
  it("timeouts abort each hung GET before another starts; the three-GET cycle ends with none active", async () => {
    vi.useFakeTimers();
    const h = harness();
    await act(() => h.view.result.current.estimate());
    let confirmation!: Promise<void>;
    act(() => { confirmation = h.view.result.current.confirm(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(35_000); await confirmation; });
    expect(h.view.result.current.phase).toBe("querying");
    expect(h.view.result.current.isLookingUp).toBe(false);
    expect(h.submit).toHaveBeenCalledTimes(1);
    expect(h.requestPaths.every((url) => url.endsWith(`/ecom_cutout/${key}`))).toBe(true);
    expect(h.peak()).toBeLessThanOrEqual(1);
    expect(h.active()).toBe(0);
    expect(h.transport).toHaveBeenCalledTimes(3);
    expect(h.signals.every((signal) => signal?.aborted)).toBe(true);
  });

  it("pause then immediate continue cancels the previous GET before releasing ownership", async () => {
    vi.useFakeTimers();
    const h = harness();
    await act(() => h.view.result.current.estimate());
    act(() => { void h.view.result.current.confirm(); });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(h.active()).toBe(1);
    act(() => h.view.result.current.pauseLookup());
    act(() => { void h.view.result.current.continueLookup(); });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(h.submit).toHaveBeenCalledTimes(1);
    expect(h.peak()).toBeLessThanOrEqual(1);
    expect(h.signals[0]?.aborted).toBe(true);
    expect(h.signals[1]?.aborted).toBe(false);
    // A late catch/finally from the old cycle must not abort the new cycle.
    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(h.active()).toBe(1);
    expect(h.view.result.current.isLookingUp).toBe(true);
  });

  it("unmount aborts the live GET and settles confirm without starting another request", async () => {
    vi.useFakeTimers();
    const h = harness();
    await act(() => h.view.result.current.estimate());
    let confirmation!: Promise<void>;
    act(() => { confirmation = h.view.result.current.confirm(); });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(h.active()).toBe(1);
    h.view.unmount();
    await confirmation;
    expect(h.active()).toBe(0);
    expect(h.signals[0]?.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(35_000);
    expect(h.transport).toHaveBeenCalledTimes(1);
    expect(h.submit).toHaveBeenCalledTimes(1);
  });
});
