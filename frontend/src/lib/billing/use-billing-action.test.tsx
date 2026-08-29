import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, expectTypeOf, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import {
  getBillingOperation,
  parseBillingOperationLookup,
  type BillingLookupResultRegistry
} from "@/lib/api/billing";
import type {
  BillingOperationLookup,
  BillingQuote,
  BillingSummary,
  ExtendedBillingOperationLookup
} from "@/lib/api/types";

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
    operation: "cosyvoice_brand_voice_create",
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
    operation: "cosyvoice_brand_voice_create",
    idempotency_key: keyA,
    state: "completed",
    completion_kind: "succeeded",
    billing: summary,
    result_type: "brand_voice",
    result_id: "voice-1",
    resource: {
      id: "voice-1",
      name: "品牌音色",
      provider: "cosyvoice-voice-clone",
      status: "ready",
      order_status: null,
      delivery_status: "active",
      created_at: "2026-08-29T10:00:00Z"
    },
    result: {
      id: "voice-1",
      name: "品牌音色",
      provider: "cosyvoice-voice-clone",
      status: "ready",
      order_status: null,
      delivery_status: "active",
      created_at: "2026-08-29T10:00:00Z"
    },
    failure: null
  };
}

function failedLookup(): BillingOperationLookup {
  return {
    operation: "cosyvoice_brand_voice_create",
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

function inProgressLookup(): BillingOperationLookup {
  return {
    operation: "cosyvoice_brand_voice_create",
    idempotency_key: keyA,
    state: "in_progress",
    completion_kind: null,
    billing: billing("reserved"),
    result_type: null,
    result_id: null,
    resource: null,
    result: null,
    failure: null
  };
}

const notFound = () => new ApiError("未找到", "BILLING_OPERATION_NOT_FOUND", 404);
const networkError = () => new ApiError("断线", "NETWORK_ERROR", 0);

type TestInput = { text: string };
type TestResult = { id: string; billing: BillingSummary };
type CustomLookup = ExtendedBillingOperationLookup & {
  operation: "custom_operation";
  state: "completed";
  completion_kind: "succeeded";
  result_type: "custom_result";
  result_id: string;
  resource: { id: string };
  result: { id: string };
};

const customRegistry: BillingLookupResultRegistry = {
  custom_result: {
    operations: ["custom_operation"],
    completionKinds: ["succeeded"],
    parse: (value) =>
      typeof value === "object" &&
      value !== null &&
      !Array.isArray(value) &&
      Object.keys(value).length === 1 &&
      typeof (value as { id?: unknown }).id === "string"
        ? (value as Record<string, unknown>)
        : null,
    validateResultId: (resultId, payload) => payload?.id === resultId
  }
};

function customLookup(): CustomLookup {
  return {
    operation: "custom_operation",
    idempotency_key: keyA,
    state: "completed",
    completion_kind: "succeeded",
    billing: billing(),
    result_type: "custom_result",
    result_id: "custom-1",
    resource: { id: "custom-1" },
    result: { id: "custom-1" },
    failure: null
  };
}

function parseCustomLookup(value: unknown): CustomLookup | null {
  const parsed = parseBillingOperationLookup(value, customRegistry);
  return parsed?.operation === "custom_operation" &&
    parsed.state === "completed" &&
    parsed.completion_kind === "succeeded" &&
    parsed.result_type === "custom_result" &&
    typeof parsed.result.id === "string" &&
    parsed.resource?.id === parsed.result.id
    ? (parsed as CustomLookup)
    : null;
}

function options(
  overrides: Partial<UseBillingActionOptions<TestInput, BillingQuote, TestResult>> = {}
): UseBillingActionOptions<TestInput, BillingQuote, TestResult> {
  return {
    operation: "cosyvoice_brand_voice_create",
    input: { text: "你好" },
    estimate: vi.fn().mockResolvedValue(quote()),
    submit: vi.fn().mockResolvedValue({ id: "voice-1", billing: billing() }),
    lookup: vi.fn().mockResolvedValue(completedLookup()),
    resultFromLookup: (lookup) =>
      lookup.state === "completed" &&
      lookup.completion_kind === "succeeded" &&
      lookup.operation === "cosyvoice_brand_voice_create" &&
      lookup.result_type === "brand_voice" &&
      lookup.result.id === lookup.result_id &&
      lookup.resource?.id === lookup.result_id
        ? { id: lookup.result.id, billing: lookup.billing }
        : null,
    createIdempotencyKey: vi.fn().mockReturnValue(keyA),
    ...overrides
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
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

  it("does not let an older run unlock a newer pending submission", async () => {
    const inputA = { text: "A" };
    const inputB = { text: "B" };
    let resolveA!: (value: TestResult) => void;
    let resolveB!: (value: TestResult) => void;
    const submitA = vi.fn(() => new Promise<TestResult>((resolve) => (resolveA = resolve)));
    const pendingB = new Promise<TestResult>((resolve) => (resolveB = resolve));
    const submitB = vi.fn(() => pendingB);
    const deps = options({ input: inputA, submit: submitA });
    const view = renderHook(
      ({ input, submit }) => useBillingAction({ ...deps, input, submit }),
      { initialProps: { input: inputA, submit: submitA } }
    );
    await act(() => view.result.current.estimate());
    let runA!: Promise<void>;
    act(() => {
      runA = view.result.current.confirm();
    });
    expect(submitA).toHaveBeenCalledTimes(1);

    view.rerender({ input: inputB, submit: submitB });
    await waitFor(() => expect(view.result.current.phase).toBe("idle"));
    await act(() => view.result.current.estimate());
    let runB!: Promise<void>;
    act(() => {
      runB = view.result.current.confirm();
    });
    expect(submitB).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveA({ id: "voice-a", billing: billing() });
      await runA;
    });
    act(() => {
      void view.result.current.confirm();
    });
    expect(submitB).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveB({ id: "voice-b", billing: billing() });
      await runB;
    });
  });

  it("does not let an older lookup overwrite a newer terminal result", async () => {
    const inputA = { text: "A" };
    const inputB = { text: "B" };
    let resolveLookupA!: (value: BillingOperationLookup) => void;
    const lookupA = vi.fn(
      () => new Promise<BillingOperationLookup>((resolve) => (resolveLookupA = resolve))
    );
    const submitA = vi.fn().mockRejectedValue(networkError());
    const submitB = vi.fn().mockResolvedValue({ id: "voice-b", billing: billing() });
    const deps = options({ input: inputA, submit: submitA, lookup: lookupA });
    const view = renderHook(
      ({ input, submit, lookup }) => useBillingAction({ ...deps, input, submit, lookup }),
      { initialProps: { input: inputA, submit: submitA, lookup: lookupA } }
    );
    await act(() => view.result.current.estimate());
    let runA!: Promise<void>;
    act(() => {
      runA = view.result.current.confirm();
    });
    await waitFor(() => expect(lookupA).toHaveBeenCalledTimes(1));

    const lookupB = vi.fn().mockResolvedValue(completedLookup());
    view.rerender({ input: inputB, submit: submitB, lookup: lookupB });
    await waitFor(() => expect(view.result.current.phase).toBe("idle"));
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());
    expect(view.result.current.phase).toBe("succeeded");
    expect(view.result.current.result?.id).toBe("voice-b");

    await act(async () => {
      resolveLookupA(inProgressLookup());
      await runA;
    });
    expect(view.result.current.phase).toBe("succeeded");
    expect(view.result.current.result?.id).toBe("voice-b");
    expect(view.result.current.lookup).toBeNull();
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

    expect(lookup).toHaveBeenCalledWith("cosyvoice_brand_voice_create", keyA);
    expect(view.result.current.billing?.status).toBe("settled");
    expect(view.result.current.phase).toBe("succeeded");
  });

  it("fails closed when the operation-specific lookup result parser returns null", async () => {
    const view = renderHook(() =>
      useBillingAction(
        options({
          submit: vi.fn().mockRejectedValue(networkError()),
          lookup: vi.fn().mockResolvedValue(completedLookup()),
          resultFromLookup: vi.fn().mockReturnValue(null)
        })
      )
    );
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());

    expect(view.result.current.phase).toBe("failed");
    expect(view.result.current.result).toBeNull();
    expect(view.result.current.errorMessage).toBe("计费结果无法确认");
  });

  it("recovers an extended lookup through an explicit typed parser", async () => {
    const view = renderHook(() =>
      useBillingAction({
        operation: "custom_operation",
        input: { text: "扩展请求" },
        estimate: vi.fn().mockResolvedValue({ ...quote(), operation: "custom_operation" }),
        submit: vi.fn().mockRejectedValue(networkError()),
        lookup: vi.fn().mockResolvedValue(customLookup()),
        parseLookup: parseCustomLookup,
        resultFromLookup: (lookup: CustomLookup) => ({
          id: lookup.result.id,
          billing: lookup.billing
        }),
        createIdempotencyKey: vi.fn().mockReturnValue(keyA)
      })
    );
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());

    expect(view.result.current.phase).toBe("succeeded");
    expect(view.result.current.result?.id).toBe("custom-1");
    expect(view.result.current.lookup?.result_type).toBe("custom_result");
    expectTypeOf(view.result.current.lookup).toEqualTypeOf<CustomLookup | null>();
  });

  it("passes an extended parser through the hook default lookup transport", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ data: customLookup(), error: null, request_id: "req-1" }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
    );
    const view = renderHook(() =>
      useBillingAction({
        operation: "custom_operation",
        input: { text: "扩展请求" },
        estimate: vi.fn().mockResolvedValue({ ...quote(), operation: "custom_operation" }),
        submit: vi.fn().mockRejectedValue(networkError()),
        parseLookup: parseCustomLookup,
        resultFromLookup: (lookup: CustomLookup) => ({
          id: lookup.result.id,
          billing: lookup.billing
        }),
        createIdempotencyKey: vi.fn().mockReturnValue(keyA)
      })
    );
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());

    expect(view.result.current.phase).toBe("succeeded");
    expect(view.result.current.result?.id).toBe("custom-1");
  });

  it("rejects a malformed extended lookup before mapping TResult", async () => {
    const malformed = {
      ...customLookup(),
      result: { id: "custom-1", unexpected: true }
    };
    const mapResult = vi.fn((lookup: CustomLookup) => ({
      id: lookup.result.id,
      billing: lookup.billing
    }));
    const view = renderHook(() =>
      useBillingAction({
        operation: "custom_operation",
        input: { text: "扩展请求" },
        estimate: vi.fn().mockResolvedValue({ ...quote(), operation: "custom_operation" }),
        submit: vi.fn().mockRejectedValue(networkError()),
        lookup: vi.fn().mockResolvedValue(malformed),
        parseLookup: parseCustomLookup,
        resultFromLookup: mapResult,
        createIdempotencyKey: vi.fn().mockReturnValue(keyA)
      })
    );
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());

    expect(view.result.current.phase).toBe("querying");
    expect(view.result.current.result).toBeNull();
    expect(view.result.current.errorMessage).toBe("计费结果协议异常，请继续查询");
    expect(mapResult).not.toHaveBeenCalled();
  });

  it("keeps valid billing visible for a malformed canonical payload and allows explicit lookup continuation", async () => {
    const malformed = {
      ...completedLookup(),
      result: { ...completedLookup().result, status: "processing" },
      resource: { ...completedLookup().resource, status: "processing" }
    };
    const lookup = vi
      .fn()
      .mockResolvedValueOnce(malformed)
      .mockResolvedValueOnce(completedLookup());
    const view = renderHook(() =>
      useBillingAction(
        options({
          submit: vi.fn().mockRejectedValue(networkError()),
          lookup
        })
      )
    );
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());

    expect(view.result.current.phase).toBe("querying");
    expect(view.result.current.billing).toEqual(billing("settled"));
    expect(view.result.current.result).toBeNull();
    expect(view.result.current.errorMessage).toBe("计费结果协议异常，请继续查询");

    await act(() => view.result.current.continueLookup());
    expect(view.result.current.phase).toBe("succeeded");
    expect(view.result.current.result?.id).toBe("voice-1");
    expect(lookup).toHaveBeenCalledTimes(2);
  });

  it("preserves billing from an HTTP 200 protocol error and recovers through the default transport", async () => {
    const malformed = {
      ...completedLookup(),
      result_type: "unknown_result",
      result_id: "unknown-1",
      resource: { id: "unknown-1" },
      result: { id: "unknown-1" }
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ data: malformed, error: null, request_id: "req-malformed" }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ data: completedLookup(), error: null, request_id: "req-valid" }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      );
    vi.stubGlobal("fetch", fetchMock);
    const view = renderHook(() =>
      useBillingAction(
        options({
          submit: vi.fn().mockRejectedValue(networkError()),
          lookup: undefined
        })
      )
    );
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());

    expect(view.result.current.phase).toBe("querying");
    expect(view.result.current.billing).toEqual(billing("settled"));
    expect(view.result.current.result).toBeNull();
    expect(view.result.current.errorMessage).toBe("计费结果协议异常，请继续查询");

    await act(() => view.result.current.continueLookup());
    expect(view.result.current.phase).toBe("succeeded");
    expect(view.result.current.result?.id).toBe("voice-1");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps the lookup parser and TResult mapper frozen during recovery", async () => {
    let resolveLookup!: (value: unknown) => void;
    const lookup = vi.fn(() => new Promise<unknown>((resolve) => (resolveLookup = resolve)));
    const originalParser = vi.fn(parseCustomLookup);
    const originalMapper = vi.fn((value: CustomLookup) => ({
      id: value.result.id,
      billing: value.billing
    }));
    const replacementParser = vi.fn(parseCustomLookup);
    const replacementMapper = vi.fn((value: CustomLookup) => ({
      id: `replacement-${value.result.id}`,
      billing: value.billing
    }));
    const view = renderHook(
      ({ parseLookup, resultFromLookup }) =>
        useBillingAction({
          operation: "custom_operation",
          input: { text: "扩展请求" },
          estimate: vi.fn().mockResolvedValue({ ...quote(), operation: "custom_operation" }),
          submit: vi.fn().mockRejectedValue(networkError()),
          lookup,
          parseLookup,
          resultFromLookup,
          createIdempotencyKey: vi.fn().mockReturnValue(keyA)
        }),
      {
        initialProps: {
          parseLookup: originalParser,
          resultFromLookup: originalMapper
        }
      }
    );
    await act(() => view.result.current.estimate());
    let confirmation!: Promise<void>;
    act(() => {
      confirmation = view.result.current.confirm();
    });
    await waitFor(() => expect(lookup).toHaveBeenCalledTimes(1));

    view.rerender({
      parseLookup: replacementParser,
      resultFromLookup: replacementMapper
    });
    await act(async () => {
      resolveLookup(customLookup());
      await confirmation;
    });

    expect(view.result.current.phase).toBe("succeeded");
    expect(view.result.current.result?.id).toBe("custom-1");
    expect(originalParser).toHaveBeenCalled();
    expect(originalMapper).toHaveBeenCalledTimes(1);
    expect(replacementParser).not.toHaveBeenCalled();
    expect(replacementMapper).not.toHaveBeenCalled();
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
    expect(lookup).toHaveBeenCalledWith("cosyvoice_brand_voice_create", keyA);
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

  it("keeps recovery bound to the original submit and lookup callbacks", async () => {
    vi.useFakeTimers();
    const originalSubmit = vi
      .fn()
      .mockRejectedValueOnce(networkError())
      .mockResolvedValueOnce({ id: "voice-1", billing: billing() });
    const originalLookup = vi.fn().mockRejectedValue(notFound());
    const replacementSubmit = vi.fn();
    const replacementLookup = vi.fn();
    const deps = options({ submit: originalSubmit, lookup: originalLookup });
    const view = renderHook(
      ({ submit, lookup }) => useBillingAction({ ...deps, submit, lookup }),
      { initialProps: { submit: originalSubmit, lookup: originalLookup } }
    );
    await act(() => view.result.current.estimate());

    let confirmation!: Promise<void>;
    act(() => {
      confirmation = view.result.current.confirm();
    });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(originalLookup).toHaveBeenCalledTimes(1);

    view.rerender({ submit: replacementSubmit, lookup: replacementLookup });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
      await confirmation;
    });

    expect(originalLookup).toHaveBeenCalledTimes(3);
    expect(replacementLookup).not.toHaveBeenCalled();
    expect(originalSubmit).toHaveBeenCalledTimes(2);
    expect(replacementSubmit).not.toHaveBeenCalled();
    expect(view.result.current.phase).toBe("succeeded");
  });

  it("cancels recovery without using a replacement operation or callbacks", async () => {
    let resolveLookup!: (value: BillingOperationLookup) => void;
    const originalLookup = vi.fn(
      () => new Promise<BillingOperationLookup>((resolve) => (resolveLookup = resolve))
    );
    const originalSubmit = vi.fn().mockRejectedValue(networkError());
    const replacementLookup = vi.fn();
    const replacementSubmit = vi.fn();
    const deps = options({ submit: originalSubmit, lookup: originalLookup });
    const view = renderHook(
      ({ operation, submit, lookup }) =>
        useBillingAction({ ...deps, operation, submit, lookup }),
      {
        initialProps: {
          operation: "cosyvoice_brand_voice_create",
          submit: originalSubmit,
          lookup: originalLookup
        }
      }
    );
    await act(() => view.result.current.estimate());
    let confirmation!: Promise<void>;
    act(() => {
      confirmation = view.result.current.confirm();
    });
    await waitFor(() => expect(originalLookup).toHaveBeenCalledTimes(1));

    view.rerender({
      operation: "doubao_brand_voice_order_create",
      submit: replacementSubmit,
      lookup: replacementLookup
    });
    await waitFor(() => expect(view.result.current.phase).toBe("idle"));
    await act(async () => {
      resolveLookup(completedLookup());
      await confirmation;
    });

    expect(originalLookup).toHaveBeenCalledWith("cosyvoice_brand_voice_create", keyA);
    expect(replacementLookup).not.toHaveBeenCalled();
    expect(replacementSubmit).not.toHaveBeenCalled();
    expect(view.result.current.phase).toBe("idle");
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

  it("invalidates a pending POST result when the same input object mutates and rerenders", async () => {
    const input = { text: "原请求" };
    let resolveSubmit!: (value: TestResult) => void;
    const submit = vi.fn(
      () => new Promise<TestResult>((resolve) => (resolveSubmit = resolve))
    );
    const deps = options({ input, submit });
    const view = renderHook(
      ({ currentInput }) => useBillingAction({ ...deps, input: currentInput }),
      { initialProps: { currentInput: input } }
    );
    await act(() => view.result.current.estimate());
    let confirmation!: Promise<void>;
    act(() => {
      confirmation = view.result.current.confirm();
    });

    input.text = "已突变";
    view.rerender({ currentInput: input });
    await act(async () => {
      resolveSubmit({ id: "voice-1", billing: billing() });
      await confirmation;
    });

    expect(view.result.current.phase).toBe("idle");
    expect(view.result.current.quote).toBeNull();
    expect(view.result.current.idempotencyKey).toBeNull();
    expect(view.result.current.result).toBeNull();
  });

  it("invalidates a pending lookup when the same input object mutates and rerenders", async () => {
    const input = { text: "原请求" };
    let resolveLookup!: (value: BillingOperationLookup) => void;
    const lookup = vi.fn(
      () => new Promise<BillingOperationLookup>((resolve) => (resolveLookup = resolve))
    );
    const deps = options({ input, submit: vi.fn().mockRejectedValue(networkError()), lookup });
    const view = renderHook(
      ({ currentInput }) => useBillingAction({ ...deps, input: currentInput }),
      { initialProps: { currentInput: input } }
    );
    await act(() => view.result.current.estimate());
    let confirmation!: Promise<void>;
    act(() => {
      confirmation = view.result.current.confirm();
    });
    await waitFor(() => expect(lookup).toHaveBeenCalledTimes(1));

    input.text = "已突变";
    view.rerender({ currentInput: input });
    await act(async () => {
      resolveLookup(completedLookup());
      await confirmation;
    });

    expect(view.result.current.phase).toBe("idle");
    expect(view.result.current.quote).toBeNull();
    expect(view.result.current.billing).toBeNull();
  });

  it("rejects a quote for a different operation", async () => {
    const wrongQuote = { ...quote(), operation: "video_create" } as BillingQuote;
    const view = renderHook(() =>
      useBillingAction(options({ estimate: vi.fn().mockResolvedValue(wrongQuote) }))
    );
    await act(() => view.result.current.estimate());
    expect(view.result.current.phase).toBe("failed");
    expect(view.result.current.quote).toBeNull();
    expect(view.result.current.canConfirm).toBe(false);
  });

  it("clears a ready quote when the operation changes", async () => {
    const deps = options();
    const view = renderHook(
      ({ operation }) => useBillingAction({ ...deps, operation }),
      { initialProps: { operation: "cosyvoice_brand_voice_create" } }
    );
    await act(() => view.result.current.estimate());
    expect(view.result.current.phase).toBe("ready");

    view.rerender({ operation: "doubao_brand_voice_order_create" });

    await waitFor(() => expect(view.result.current.phase).toBe("idle"));
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
    const lookup = vi
      .fn()
      .mockResolvedValueOnce(failedLookup())
      .mockResolvedValueOnce(completedLookup());
    const view = renderHook(() =>
      useBillingAction(
        options({
          submit: vi.fn().mockRejectedValue(networkError()),
          lookup
        })
      )
    );
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());
    expect(view.result.current.phase).toBe("failed");
    expect(view.result.current.billing?.status).toBe("released");
    expect(view.result.current.error).toMatchObject({ code: "PROVIDER_FAILED" });

    await act(() => view.result.current.continueLookup());
    expect(lookup).toHaveBeenCalledTimes(1);
    expect(view.result.current.phase).toBe("failed");
  });

  it("requires a strict parser before narrowing the canonical lookup mapper type", () => {
    type VoiceSucceeded = Extract<
      BillingOperationLookup,
      { completion_kind: "succeeded"; result_type: "brand_voice" }
    >;
    const voice = completedLookup() as VoiceSucceeded;
    const useCompileContracts = () => {
      // @ts-expect-error canonical brand_voice results cannot claim the video_create operation
      const mismatchedOperation: VoiceSucceeded = { ...voice, operation: "video_create" };

      // @ts-expect-error narrowing the canonical lookup generic requires an explicit strict parser
      useBillingAction<TestInput, BillingQuote, TestResult, VoiceSucceeded>({
        ...options(),
        resultFromLookup: (lookup) => ({ id: lookup.result.id, billing: lookup.billing })
      });

      // @ts-expect-error a custom mapper is unavailable without its required strict parser
      useBillingAction<TestInput, BillingQuote, TestResult, CustomLookup>({
        operation: "custom_operation",
        input: { text: "扩展请求" },
        estimate: vi.fn().mockResolvedValue({ ...quote(), operation: "custom_operation" }),
        submit: vi.fn().mockRejectedValue(networkError()),
        resultFromLookup: (lookup) => ({ id: lookup.result.id, billing: lookup.billing })
      });

      // @ts-expect-error getBillingOperation narrowing also requires an explicit strict parser
      getBillingOperation<VoiceSucceeded>("video_create", keyA);
      return mismatchedOperation;
    };

    expect(useCompileContracts).toBeTypeOf("function");
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

  it.each(["released", "reserved", "settled", "partially_settled"] as const)(
    "lets PRICE_CHANGED override %s billing details",
    async (status) => {
    const lookup = vi.fn();
    const view = renderHook(() =>
      useBillingAction(
        options({
          lookup,
          submit: vi.fn().mockRejectedValue(
            new ApiError("价格变化", "PRICE_CHANGED", 422, { billing: billing(status) })
          )
        })
      )
    );
    await act(() => view.result.current.estimate());
    await act(() => view.result.current.confirm());

    expect(view.result.current.phase).toBe("failed");
    expect(view.result.current.quote).toBeNull();
    expect(view.result.current.idempotencyKey).toBeNull();
    expect(lookup).not.toHaveBeenCalled();
    }
  );

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
