import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import type { BillingOperationLookup, BillingQuote, BillingSummary } from "@/lib/api/types";

import { useBillingAction, type UseBillingActionOptions } from "./use-billing-action";

const keyA = "11111111-1111-4111-8111-111111111111";
const keyB = "22222222-2222-4222-8222-222222222222";

function billing(status: BillingSummary["status"] = "settled"): BillingSummary {
  const amounts = {
    reserved: [30, 30, 0, 0],
    settled: [30, 0, 30, 0],
    partially_settled: [30, 0, 20, 10],
    released: [30, 0, 0, 30]
  }[status];
  return {
    operation_id: "op-1",
    idempotency_key: keyA,
    status,
    requested_credits: amounts[0],
    held_credits: amounts[1],
    settled_credits: amounts[2],
    released_credits: amounts[3]
  };
}

function quote(expiresAt = Date.now() + 60_000): BillingQuote {
  return {
    pricing_contract: "billing_quote",
    operation: "voice_clone",
    pricing_shape: "simple",
    unit: "character",
    quantity: "100",
    unit_credits: "0.3",
    rate_scope: "tenant_overridable",
    rate_source: "tenant_rate",
    subtotal_credits: "30",
    payable_credits: 30,
    breakdown: [],
    disclosures: [],
    quote_token: "signed-token",
    expires_at: new Date(expiresAt).toISOString()
  };
}

function completedLookup(
  summary: BillingSummary = billing()
): BillingOperationLookup {
  return {
    operation: "voice_clone",
    idempotency_key: keyA,
    state: "completed",
    completion_kind: "succeeded",
    billing: summary,
    result_type: "voice",
    result_id: "voice-1",
    resource: null,
    result: { id: "voice-1" },
    failure: null
  };
}

function failedLookup(): BillingOperationLookup {
  return {
    operation: "voice_clone",
    idempotency_key: keyA,
    state: "completed",
    completion_kind: "failed",
    billing: billing("released"),
    result_type: null,
    result_id: null,
    resource: null,
    result: null,
    failure: { code: "PROVIDER_FAILED", original_http_status: 502, detail: null }
  };
}

const notFound = () => new ApiError("未找到", "BILLING_OPERATION_NOT_FOUND", 404);
const networkError = () => new ApiError("断线", "NETWORK_ERROR", 0);

type TestInput = { text: string };
type TestResult = { id: string; billing: BillingSummary };

function options(
  overrides: Partial<UseBillingActionOptions<TestInput, BillingQuote, TestResult>> = {}
): UseBillingActionOptions<TestInput, BillingQuote, TestResult> {
  return {
    operation: "voice_clone",
    input: { text: "你好" },
    estimate: vi.fn().mockResolvedValue(quote()),
    submit: vi.fn().mockResolvedValue({ id: "voice-1", billing: billing() }),
    lookup: vi.fn().mockResolvedValue(completedLookup()),
    createIdempotencyKey: vi.fn().mockReturnValue(keyA),
    ...overrides
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("useBillingAction", () => {
  it("estimates a parsed server quote and binds confirmation to one UUID", async () => {
    const deps = options();
    const view = renderHook(() => useBillingAction(deps));

    await act(() => view.result.current.estimate());
    expect(view.result.current.phase).toBe("ready");
    expect(view.result.current.quote?.payable_credits).toBe(30);

    await act(() => view.result.current.confirm());
    expect(deps.submit).toHaveBeenCalledWith(
      deps.input,
      { idempotency_key: keyA, quote_token: "signed-token" }
    );
    expect(view.result.current.phase).toBe("succeeded");
    expect(deps.createIdempotencyKey).toHaveBeenCalledTimes(1);
  });

  it("exposes estimation loading and counts down to an expired quote that can be re-estimated", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-29T10:00:00Z"));
    let resolveFirst!: (value: BillingQuote) => void;
    const estimate = vi
      .fn()
      .mockImplementationOnce(() => new Promise<BillingQuote>((resolve) => (resolveFirst = resolve)))
      .mockImplementationOnce(async () => quote(Date.now() + 30_000));
    const view = renderHook(() => useBillingAction(options({ estimate })));

    let first!: Promise<void>;
    act(() => {
      first = view.result.current.estimate();
    });
    expect(view.result.current.phase).toBe("estimating");
    await act(async () => {
      resolveFirst(quote(Date.now() + 2_500));
      await first;
    });
    expect(view.result.current.expiresInSeconds).toBe(3);

    await act(() => vi.advanceTimersByTimeAsync(3_000));
    expect(view.result.current.phase).toBe("expired");
    expect(view.result.current.canConfirm).toBe(false);

    await act(() => view.result.current.estimate());
    expect(view.result.current.phase).toBe("ready");
    expect(view.result.current.expiresInSeconds).toBe(30);
  });

  it("clears the quote and key when the normalized input changes", async () => {
    const deps = options();
    const { result, rerender } = renderHook(
      ({ input }) => useBillingAction({ ...deps, input }),
      { initialProps: { input: { text: "你好", nested: { b: 2, a: 1 } } } }
    );
    await act(() => result.current.estimate());
    expect(result.current.quote).not.toBeNull();

    rerender({ input: { text: "已变化", nested: { a: 1, b: 2 } } });
    await waitFor(() => expect(result.current.quote).toBeNull());
    expect(result.current.idempotencyKey).toBeNull();
  });

  it("guards double confirmation synchronously", async () => {
    let resolve!: (value: { id: string; billing: BillingSummary }) => void;
    const submit = vi.fn(() => new Promise<{ id: string; billing: BillingSummary }>((done) => (resolve = done)));
    const deps = options({ submit });
    const view = renderHook(() => useBillingAction(deps));
    await act(() => view.result.current.estimate());

    let first!: Promise<void>;
    act(() => {
      first = view.result.current.confirm();
      void view.result.current.confirm();
    });
    expect(submit).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolve({ id: "voice-1", billing: billing() });
      await first;
    });
  });

  it("queries the original operation after an unknown network result", async () => {
    const lookup = vi.fn().mockResolvedValue(completedLookup());
    const deps = options({
      submit: vi.fn().mockRejectedValue(new ApiError("断线", "NETWORK_ERROR", 0)),
      lookup
    });
    const view = renderHook(() => useBillingAction(deps));
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());

    expect(lookup).toHaveBeenCalledWith("voice_clone", keyA);
    expect(view.result.current.billing?.status).toBe("settled");
    expect(view.result.current.phase).toBe("succeeded");
  });

  it("queries instead of treating non-terminal billing in an error as a known failure", async () => {
    const lookup = vi.fn().mockResolvedValue(completedLookup());
    const deps = options({
      submit: vi.fn().mockRejectedValue(
        new ApiError("处理中", "UPSTREAM_TIMEOUT", 502, { billing: billing("reserved") })
      ),
      lookup
    });
    const view = renderHook(() => useBillingAction(deps));
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());
    expect(lookup).toHaveBeenCalledWith("voice_clone", keyA);
    expect(view.result.current.phase).toBe("succeeded");
  });

  it("replays once after three 404 lookups using the exact original key and token", async () => {
    vi.useFakeTimers();
    const submit = vi
      .fn()
      .mockRejectedValueOnce(networkError())
      .mockResolvedValueOnce({ id: "voice-1", billing: billing() });
    const lookup = vi.fn().mockRejectedValue(notFound());
    const deps = options({ submit, lookup });
    const view = renderHook(() => useBillingAction(deps));
    await act(() => view.result.current.estimate());

    let confirmation!: Promise<void>;
    act(() => {
      confirmation = view.result.current.confirm();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
      await confirmation;
    });

    expect(lookup).toHaveBeenCalledTimes(3);
    expect(submit).toHaveBeenCalledTimes(2);
    expect(submit.mock.calls[0]).toEqual(submit.mock.calls[1]);
    expect(submit.mock.calls[1][1]).toEqual({
      idempotency_key: keyA,
      quote_token: "signed-token"
    });
    expect(view.result.current.phase).toBe("succeeded");
  });

  it("does not replay when the bound input object was mutated without a render", async () => {
    vi.useFakeTimers();
    const submit = vi.fn().mockRejectedValue(networkError());
    const lookup = vi.fn().mockRejectedValue(notFound());
    const deps = options({ submit, lookup });
    const view = renderHook(() => useBillingAction(deps));
    await act(() => view.result.current.estimate());

    let confirmation!: Promise<void>;
    act(() => {
      confirmation = view.result.current.confirm();
    });
    deps.input!.text = "突变";
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
      await confirmation;
    });
    expect(lookup).toHaveBeenCalledTimes(3);
    expect(submit).toHaveBeenCalledTimes(1);
    expect(view.result.current.quote).toBeNull();
    expect(view.result.current.idempotencyKey).toBeNull();
  });

  it("never replays twice and leaves a second unknown result queryable", async () => {
    vi.useFakeTimers();
    const submit = vi.fn().mockRejectedValue(networkError());
    const lookup = vi.fn().mockRejectedValue(notFound());
    const view = renderHook(() => useBillingAction(options({ submit, lookup })));
    await act(() => view.result.current.estimate());

    let confirmation!: Promise<void>;
    act(() => {
      confirmation = view.result.current.confirm();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
      await confirmation;
    });

    expect(submit).toHaveBeenCalledTimes(2);
    expect(lookup).toHaveBeenCalledTimes(6);
    expect(view.result.current.phase).toBe("querying");
    expect(view.result.current.errorMessage).toBe("计费结果确认中");

    lookup.mockResolvedValueOnce(completedLookup());
    await act(() => view.result.current.continueLookup());
    expect(view.result.current.phase).toBe("succeeded");
    expect(submit).toHaveBeenCalledTimes(2);
  });

  it("does not replay after the quote expires during persistent 404 lookup", async () => {
    vi.useFakeTimers();
    const submit = vi.fn().mockRejectedValue(networkError());
    const lookup = vi.fn().mockRejectedValue(notFound());
    const view = renderHook(() =>
      useBillingAction(options({ estimate: vi.fn().mockResolvedValue(quote(Date.now() + 1_500)), submit, lookup }))
    );
    await act(() => view.result.current.estimate());

    let confirmation!: Promise<void>;
    act(() => {
      confirmation = view.result.current.confirm();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
      await confirmation;
    });

    expect(lookup).toHaveBeenCalledTimes(3);
    expect(submit).toHaveBeenCalledTimes(1);
    expect(view.result.current.phase).toBe("expired");
    expect(view.result.current.quote).toBeNull();
  });

  it("treats a visible failed lookup returned with HTTP 200 as failure", async () => {
    const view = renderHook(() =>
      useBillingAction(
        options({
          submit: vi.fn().mockRejectedValue(networkError()),
          lookup: vi.fn().mockResolvedValue(failedLookup())
        })
      )
    );
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());
    expect(view.result.current.phase).toBe("failed");
    expect(view.result.current.billing?.status).toBe("released");
    expect(view.result.current.error).toMatchObject({ code: "PROVIDER_FAILED" });
  });

  it("clears PRICE_CHANGED and creates a new key only after explicit retry and re-estimate", async () => {
    const createIdempotencyKey = vi.fn().mockReturnValueOnce(keyA).mockReturnValueOnce(keyB);
    const submit = vi
      .fn()
      .mockRejectedValueOnce(new ApiError("价格变化", "PRICE_CHANGED", 422))
      .mockResolvedValueOnce({
        id: "voice-2",
        billing: { ...billing(), idempotency_key: keyB }
      });
    const view = renderHook(() => useBillingAction(options({ createIdempotencyKey, submit })));
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());
    expect(view.result.current.phase).toBe("failed");
    expect(view.result.current.quote).toBeNull();

    act(() => view.result.current.retry());
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());
    expect(submit.mock.calls[1][1].idempotency_key).toBe(keyB);
    expect(view.result.current.phase).toBe("succeeded");
  });

  it("does not reuse a completed attempt and generates a fresh quote and key after explicit retry", async () => {
    const createIdempotencyKey = vi.fn().mockReturnValueOnce(keyA).mockReturnValueOnce(keyB);
    const submit = vi
      .fn()
      .mockResolvedValueOnce({ id: "voice-1", billing: billing() })
      .mockResolvedValueOnce({
        id: "voice-2",
        billing: { ...billing(), idempotency_key: keyB }
      });
    const estimate = vi.fn().mockResolvedValue(quote());
    const view = renderHook(() =>
      useBillingAction(options({ createIdempotencyKey, estimate, submit }))
    );
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());
    await act(() => view.result.current.confirm());
    expect(submit).toHaveBeenCalledTimes(1);

    act(() => view.result.current.retry());
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());
    expect(estimate).toHaveBeenCalledTimes(2);
    expect(submit.mock.calls[1][1].idempotency_key).toBe(keyB);
  });

  it("keeps confirmation unavailable when estimation fails", async () => {
    const submit = vi.fn();
    const view = renderHook(() =>
      useBillingAction(options({ estimate: vi.fn().mockRejectedValue(new Error("offline")), submit }))
    );
    await act(() => view.result.current.estimate());
    expect(view.result.current.phase).toBe("failed");
    expect(view.result.current.quote).toBeNull();
    expect(view.result.current.canConfirm).toBe(false);
    await act(() => view.result.current.confirm());
    expect(submit).not.toHaveBeenCalled();
  });

  it("cancels expiry and lookup timers on unmount", async () => {
    vi.useFakeTimers();
    const view = renderHook(() =>
      useBillingAction(
        options({ submit: vi.fn().mockRejectedValue(networkError()), lookup: vi.fn().mockRejectedValue(notFound()) })
      )
    );
    await act(() => view.result.current.estimate());
    act(() => {
      void view.result.current.confirm();
    });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
