import { act, render, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

// Mock the videos API so no real fetch happens.
vi.mock("@/lib/api/videos", () => ({
  listVideos: vi.fn().mockResolvedValue([]),
  createVideo: vi.fn().mockResolvedValue({
    id: "t1",
    task_id: "t1",
    status: "queued",
    pricing_contract: "legacy_estimate"
  }),
  estimateVideo: vi.fn(),
  getVideo: vi.fn().mockResolvedValue({}),
  streamVideoEvents: vi.fn().mockResolvedValue(undefined)
}));

vi.mock("@/lib/api/billing", async (loadOriginal) => ({
  ...(await loadOriginal<typeof import("@/lib/api/billing")>()),
  getBillingOperation: vi.fn()
}));

// Controllable auth session.
let mockSession: { token: string } | null = null;
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: mockSession, ready: true, login: vi.fn(), logout: vi.fn() })
}));

// Tiny windows so the watchdog trips inside the fake-timer advance.
vi.mock("@/lib/sse/constants", async (orig) => ({
  ...(await orig<typeof import("@/lib/sse/constants")>()),
  STALL_MS: 50,
  HARD_CAP_MS: 10_000,
  POLL_MS: 10_000
}));

import { billingFromApiError, getBillingOperation } from "@/lib/api/billing";
import { createVideo, estimateVideo, listVideos, streamVideoEvents } from "@/lib/api/videos";
import { ApiError } from "@/lib/api/client";
import type { BillingOperationLookupFor, BillingSummary, VideoEstimateContract } from "@/lib/api/types";
import type { VideoPricingAttempt } from "./tasks-context";

import { VideoTasksProvider, useVideoTasks } from "./tasks-context";

const oldConfirmation = {
  quote_token: "old-video-token",
  idempotency_key: "11111111-1111-4111-8111-111111111111"
};

const billedQuote = (
  token = oldConfirmation.quote_token,
  expiresAt = "2030-01-01T00:00:00Z"
): Extract<VideoEstimateContract, { pricing_contract: "billing_quote" }> => ({
  pricing_contract: "billing_quote" as const,
  pricing_shape: "composite" as const,
  operation: "video_create",
  unit: null,
  quantity: null,
  unit_credits: null,
  rate_scope: null,
  rate_source: null,
  subtotal_credits: "100.0000",
  payable_credits: 100,
  breakdown: [{
    operation: "video_create",
    capability: "video",
    unit: "second",
    quantity: "1",
    unit_credits: "100.0000",
    subtotal_credits: "100.0000",
    rate_scope: "tenant_overridable" as const,
    rate_source: "code_default" as const,
    rate_id: null,
    effective_at: null,
    policy_key: "video_create",
    policy_version: 1,
    label: "视频生成"
  }],
  disclosures: [],
  quote_token: token,
  expires_at: expiresAt
});

const billedAttempt: VideoPricingAttempt = {
  estimate: billedQuote(),
  confirmation: oldConfirmation,
  pricingContext: { voice_kind: "brand", voice_provider: "doubao" }
};

function terminalBilling(
  status: "settled" | "released" | "partially_settled",
  requested = 100
): BillingSummary {
  const amounts = {
    settled: { held_credits: 0, settled_credits: requested, released_credits: 0 },
    released: { held_credits: 0, settled_credits: 0, released_credits: requested },
    partially_settled: { held_credits: 0, settled_credits: 40, released_credits: 60 }
  }[status];
  return {
    operation_id: `operation-${status}`,
    idempotency_key: oldConfirmation.idempotency_key,
    status,
    requested_credits: requested,
    ...amounts
  };
}

const pendingVideoLookup = (): BillingOperationLookupFor<"video_create"> => ({
  operation: "video_create",
  idempotency_key: oldConfirmation.idempotency_key,
  state: "in_progress",
  completion_kind: null,
  billing: {
    operation_id: "operation-pending",
    idempotency_key: oldConfirmation.idempotency_key,
    status: "reserved",
    requested_credits: 100,
    held_credits: 100,
    settled_credits: 0,
    released_credits: 0
  },
  result_type: null,
  result_id: null,
  resource: null,
  result: null,
  failure: null
});

const succeededVideoLookup = (
  taskId: string
): BillingOperationLookupFor<"video_create"> => ({
  operation: "video_create",
  idempotency_key: oldConfirmation.idempotency_key,
  state: "completed",
  completion_kind: "succeeded",
  billing: {
    operation_id: "operation-succeeded",
    idempotency_key: oldConfirmation.idempotency_key,
    status: "settled",
    requested_credits: 100,
    held_credits: 0,
    settled_credits: 100,
    released_credits: 0
  },
  result_type: "video_task",
  result_id: taskId,
  resource: { task_id: taskId, status: "done" },
  result: { task_id: taskId, status: "done" },
  failure: null
});

const failedVideoLookup = (
  billing: BillingSummary = terminalBilling("released")
): BillingOperationLookupFor<"video_create"> => ({
  operation: "video_create",
  idempotency_key: oldConfirmation.idempotency_key,
  state: "completed",
  completion_kind: "failed",
  billing,
  result_type: null,
  result_id: null,
  resource: null,
  result: null,
  failure: {
    code: "PROVIDER_FAILED",
    original_http_status: 502,
    detail: { requires_new_quote: true }
  }
});

// 会话卡即时 AI 标识徽标数据源（LABEL-TOGGLE-UI-0001）：createAndTrack 把 req.apply_visible_label
// 写入 TrackedTask.applyVisibleLabel，无需等 GET reconcile。
function LabelHarness({ apply }: { apply: boolean }) {
  const { tasks, createAndTrack } = useVideoTasks();
  return (
    <div>
      <button onClick={() => void createAndTrack({ topic: "x", apply_visible_label: apply }, "x")}>go</button>
      <span data-testid="label">{String(tasks[0]?.applyVisibleLabel ?? "none")}</span>
    </div>
  );
}

describe("tasks-context 会话卡即时 AI 标识徽标数据源 (LABEL-TOGGLE-UI-0001)", () => {
  beforeEach(() => {
    mockSession = { token: "t" };
    // 流挂起不产生终态/reconcile，确保断言的是 createAndTrack 写入的即时值。
    (streamVideoEvents as Mock).mockImplementation(() => new Promise<void>(() => {}));
  });
  afterEach(() => vi.clearAllMocks());

  it("createAndTrack({apply_visible_label:true}) → 会话卡 applyVisibleLabel=true（承重）", async () => {
    const { getByText, getByTestId } = render(
      <VideoTasksProvider>
        <LabelHarness apply />
      </VideoTasksProvider>
    );
    await act(async () => {
      getByText("go").click();
    });
    await waitFor(() => expect(getByTestId("label").textContent).toBe("true"));
  });

  it("默认关 apply_visible_label:false → 会话卡 applyVisibleLabel=false", async () => {
    const { getByText, getByTestId } = render(
      <VideoTasksProvider>
        <LabelHarness apply={false} />
      </VideoTasksProvider>
    );
    await act(async () => {
      getByText("go").click();
    });
    await waitFor(() => expect(getByTestId("label").textContent).toBe("false"));
  });
});

describe("VideoTasksProvider hydrate gating", () => {
  beforeEach(() => {
    mockSession = null;
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("does not fetch the list when there is no session", async () => {
    render(
      <VideoTasksProvider>
        <div>child</div>
      </VideoTasksProvider>
    );
    await new Promise((r) => setTimeout(r, 0)); // let mount effects flush
    expect(listVideos).not.toHaveBeenCalled();
  });

  it("fetches the list exactly once when a session is present", async () => {
    mockSession = { token: "t" };
    render(
      <VideoTasksProvider>
        <div>child</div>
      </VideoTasksProvider>
    );
    await waitFor(() => expect(listVideos).toHaveBeenCalledTimes(1));
  });
});

function Harness() {
  const { tasks, createAndTrack } = useVideoTasks();
  return (
    <div>
      <button onClick={() => void createAndTrack({ topic: "x", voice_id: "v", avatar_asset_id: "a" }, "x")}>go</button>
      <span data-testid="status">{tasks[0]?.status ?? "-"}</span>
    </div>
  );
}

describe("tasks-context client self-timeout", () => {
  beforeEach(() => {
    mockSession = { token: "t" };
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("forces failed when SSE only sends running and never a terminal frame", async () => {
    // SSE stays open emitting one running frame, never resolves/terminal.
    (streamVideoEvents as Mock).mockImplementation(async (_id, onMessage) => {
      onMessage({ status: "running", progress: 10, step: "avatar" });
      await new Promise(() => {}); // never resolves (stuck task)
    });
    const { getByText, getByTestId } = render(
      <VideoTasksProvider>
        <Harness />
      </VideoTasksProvider>
    );
    await act(async () => {
      getByText("go").click();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200); // > STALL_MS(50)
    });
    // Watchdog has already forced the terminal state synchronously during the
    // timer advance; assert directly (waitFor would deadlock under fake timers).
    expect(getByTestId("status").textContent).toBe("failed");
  });
});

function PricedHarness() {
  const { tasks, createAndTrack } = useVideoTasks();
  const [result, setResult] = useState("-");
  const [error, setError] = useState("-");
  const [errorMessage, setErrorMessage] = useState("-");
  const [billingStatus, setBillingStatus] = useState("-");
  return (
    <div>
      <button
        onClick={() => {
          void createAndTrack(
            { topic: "priced", voice_id: "brand", avatar_asset_id: "avatar" },
            "priced",
            billedAttempt
          ).then((accepted) => setResult(accepted.id)).catch((caught) => {
            setError((caught as ApiError).code);
            setErrorMessage((caught as Error).message);
            setBillingStatus(billingFromApiError(caught)?.status ?? "-");
          });
        }}
      >
        priced
      </button>
      <span data-testid="priced-result">{result}</span>
      <span data-testid="priced-error">{error}</span>
      <span data-testid="priced-error-message">{errorMessage}</span>
      <span data-testid="priced-billing-status">{billingStatus}</span>
      <span data-testid="priced-task">{tasks[0]?.taskId ?? "-"}</span>
    </div>
  );
}

describe("tasks-context priced operation recovery", () => {
  beforeEach(() => {
    mockSession = { token: "t" };
    (createVideo as Mock).mockReset();
    (estimateVideo as Mock).mockReset();
    (getBillingOperation as Mock).mockReset();
    (streamVideoEvents as Mock)
      .mockReset()
      .mockImplementation(() => new Promise<void>(() => {}));
  });

  it.each([
    {
      label: "settled",
      billing: terminalBilling("settled"),
      message: "视频生成未完成，已结算 100 积分。"
    },
    {
      label: "released",
      billing: terminalBilling("released"),
      message: "视频生成未完成，未扣款，已释放 100 积分。"
    },
    {
      label: "partially settled",
      billing: terminalBilling("partially_settled"),
      message: "视频生成未完成，已结算 40 积分，已释放 60 积分。"
    },
    {
      label: "legal zero-price",
      billing: terminalBilling("settled", 0),
      message: "视频生成未完成，本次为免费服务，未扣积分。"
    }
  ])("trusts authoritative $label POST billing and skips recovery lookup", async ({ billing, message }) => {
    (createVideo as Mock).mockRejectedValueOnce(
      new ApiError("gateway", "PROVIDER_FAILED", 503, { billing })
    );

    const view = render(
      <VideoTasksProvider>
        <PricedHarness />
      </VideoTasksProvider>
    );
    await act(async () => view.getByText("priced").click());

    await waitFor(() => expect(view.getByTestId("priced-error-message")).toHaveTextContent(message));
    expect(view.getByTestId("priced-billing-status")).toHaveTextContent(billing.status);
    expect(getBillingOperation).not.toHaveBeenCalled();
    expect(estimateVideo).not.toHaveBeenCalled();
  });
  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.clearAllMocks();
    (createVideo as Mock).mockReset().mockResolvedValue({
      id: "t1",
      task_id: "t1",
      status: "queued",
      pricing_contract: "legacy_estimate"
    });
    (estimateVideo as Mock).mockReset();
    (getBillingOperation as Mock).mockReset();
    (streamVideoEvents as Mock).mockReset().mockResolvedValue(undefined);
  });

  it("queries the original video_create/idempotency key before deciding an unknown POST result", async () => {
    (createVideo as Mock).mockRejectedValueOnce(new ApiError("network", "NETWORK_ERROR", 0));
    (getBillingOperation as Mock).mockResolvedValueOnce({
      operation: "video_create",
      idempotency_key: oldConfirmation.idempotency_key,
      state: "completed",
      completion_kind: "succeeded",
      billing: {
        operation_id: "operation-1",
        idempotency_key: oldConfirmation.idempotency_key,
        status: "settled",
        requested_credits: 100,
        held_credits: 0,
        settled_credits: 100,
        released_credits: 0
      },
      result_type: "video_task",
      result_id: "recovered-task",
      resource: { task_id: "recovered-task", status: "done" },
      result: { task_id: "recovered-task", status: "done" },
      failure: null
    });

    const view = render(
      <VideoTasksProvider>
        <PricedHarness />
      </VideoTasksProvider>
    );
    await act(async () => view.getByText("priced").click());

    await waitFor(() => expect(view.getByTestId("priced-result")).toHaveTextContent("recovered-task"));
    expect(getBillingOperation).toHaveBeenCalledWith(
      "video_create",
      oldConfirmation.idempotency_key
    );
    expect(view.getByTestId("priced-task")).toHaveTextContent("recovered-task");
  });

  it("treats a GET 200 completed failure as failure rather than a successful recovery", async () => {
    (createVideo as Mock).mockRejectedValueOnce(new ApiError("network", "NETWORK_ERROR", 0));
    (getBillingOperation as Mock).mockResolvedValueOnce({
      operation: "video_create",
      idempotency_key: oldConfirmation.idempotency_key,
      state: "completed",
      completion_kind: "failed",
      billing: {
        operation_id: "operation-2",
        idempotency_key: oldConfirmation.idempotency_key,
        status: "released",
        requested_credits: 100,
        held_credits: 0,
        settled_credits: 0,
        released_credits: 100
      },
      result_type: null,
      result_id: null,
      resource: null,
      result: null,
      failure: {
        code: "PROVIDER_FAILED",
        original_http_status: 502,
        detail: { requires_new_quote: true }
      }
    });

    const view = render(
      <VideoTasksProvider>
        <PricedHarness />
      </VideoTasksProvider>
    );
    await act(async () => view.getByText("priced").click());
    await waitFor(() => expect(view.getByTestId("priced-error")).toHaveTextContent("PROVIDER_FAILED"));
    expect(view.getByTestId("priced-task")).toHaveTextContent("-");
  });

  it("polls a pending lookup with the original operation and key until the task succeeds", async () => {
    vi.useFakeTimers();
    (createVideo as Mock).mockRejectedValueOnce(new ApiError("network", "NETWORK_ERROR", 0));
    (getBillingOperation as Mock)
      .mockResolvedValueOnce(pendingVideoLookup())
      .mockResolvedValueOnce(succeededVideoLookup("polled-task"));

    const view = render(
      <VideoTasksProvider>
        <PricedHarness />
      </VideoTasksProvider>
    );
    await act(async () => {
      view.getByText("priced").click();
      await Promise.resolve();
    });
    expect(getBillingOperation).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(view.getByTestId("priced-result")).toHaveTextContent("polled-task");
    expect(view.getByTestId("priced-task")).toHaveTextContent("polled-task");
    expect(getBillingOperation).toHaveBeenCalledTimes(2);
    expect((getBillingOperation as Mock).mock.calls).toEqual([
      ["video_create", oldConfirmation.idempotency_key],
      ["video_create", oldConfirmation.idempotency_key]
    ]);
    expect(createVideo).toHaveBeenCalledTimes(1);
    expect(estimateVideo).not.toHaveBeenCalled();
  });

  it("polls a pending lookup with the original operation and key until terminal failure", async () => {
    vi.useFakeTimers();
    (createVideo as Mock).mockRejectedValueOnce(new ApiError("network", "NETWORK_ERROR", 0));
    (getBillingOperation as Mock)
      .mockResolvedValueOnce(pendingVideoLookup())
      .mockResolvedValueOnce(failedVideoLookup());

    const view = render(
      <VideoTasksProvider>
        <PricedHarness />
      </VideoTasksProvider>
    );
    await act(async () => {
      view.getByText("priced").click();
      await Promise.resolve();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(view.getByTestId("priced-error")).toHaveTextContent("PROVIDER_FAILED");
    expect(view.getByTestId("priced-error-message")).toHaveTextContent(
      "视频生成未完成，未扣款，已释放 100 积分。"
    );
    expect(view.getByTestId("priced-billing-status")).toHaveTextContent("released");
    expect((getBillingOperation as Mock).mock.calls).toEqual([
      ["video_create", oldConfirmation.idempotency_key],
      ["video_create", oldConfirmation.idempotency_key]
    ]);
    expect(createVideo).toHaveBeenCalledTimes(1);
    expect(estimateVideo).not.toHaveBeenCalled();
  });

  it("uses completed-failure billing instead of blindly claiming release", async () => {
    (createVideo as Mock).mockRejectedValueOnce(new ApiError("network", "NETWORK_ERROR", 0));
    (getBillingOperation as Mock).mockResolvedValueOnce(
      failedVideoLookup(terminalBilling("partially_settled"))
    );

    const view = render(
      <VideoTasksProvider>
        <PricedHarness />
      </VideoTasksProvider>
    );
    await act(async () => view.getByText("priced").click());

    await waitFor(() => expect(view.getByTestId("priced-error-message")).toHaveTextContent(
      "视频生成未完成，已结算 40 积分，已释放 60 积分。"
    ));
    expect(view.getByTestId("priced-billing-status")).toHaveTextContent("partially_settled");
    expect(getBillingOperation).toHaveBeenCalledWith(
      "video_create",
      oldConfirmation.idempotency_key
    );
  });

  it("stops bounded lookup failures as uncertain without resubmitting or re-estimating", async () => {
    vi.useFakeTimers();
    (createVideo as Mock).mockRejectedValueOnce(new ApiError("network", "NETWORK_ERROR", 0));
    (getBillingOperation as Mock).mockRejectedValue(
      new ApiError("gateway", "HTTP_ERROR", 503)
    );

    const view = render(
      <VideoTasksProvider>
        <PricedHarness />
      </VideoTasksProvider>
    );
    await act(async () => {
      view.getByText("priced").click();
      await Promise.resolve();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(view.getByTestId("priced-error-message")).toHaveTextContent("计费结果确认中");
    expect(getBillingOperation).toHaveBeenCalledTimes(3);
    expect((getBillingOperation as Mock).mock.calls).toEqual([
      ["video_create", oldConfirmation.idempotency_key],
      ["video_create", oldConfirmation.idempotency_key],
      ["video_create", oldConfirmation.idempotency_key]
    ]);
    expect(createVideo).toHaveBeenCalledTimes(1);
    expect(estimateVideo).not.toHaveBeenCalled();
  });
});

function RetryPricedHarness() {
  const { tasks, createAndTrack, retryTask } = useVideoTasks();
  const [retryResult, setRetryResult] = useState("-");
  const [retryError, setRetryError] = useState("-");
  return (
    <div>
      <button
        onClick={() => void createAndTrack(
          { topic: "retry", voice_id: "brand", avatar_asset_id: "avatar" },
          "retry",
          billedAttempt
        )}
      >
        create-priced
      </button>
      <button
        disabled={!tasks[0] || tasks[0].status !== "failed"}
        onClick={() => void retryTask(tasks[0].taskId)
          .then(setRetryResult)
          .catch((caught) => setRetryError((caught as ApiError).code))}
      >
        retry-priced
      </button>
      <span data-testid="retry-status">{tasks[0]?.status ?? "-"}</span>
      <span data-testid="retry-result">{retryResult}</span>
      <span data-testid="retry-error">{retryError}</span>
    </div>
  );
}

describe("tasks-context explicit priced retry", () => {
  beforeEach(() => {
    mockSession = { token: "t" };
    (streamVideoEvents as Mock).mockImplementation(async (_id, onMessage) => {
      onMessage({ status: "failed", progress: 20, error_message: "provider failed" });
      await new Promise(() => {});
    });
  });
  afterEach(() => vi.clearAllMocks());

  it("gets a fresh estimate and key only after explicit retry", async () => {
    const freshKey = "22222222-2222-4222-8222-222222222222";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(freshKey);
    (createVideo as Mock).mockImplementation(async (_request, confirmation) => {
      const key = confirmation?.idempotency_key ?? oldConfirmation.idempotency_key;
      const id = key === oldConfirmation.idempotency_key ? "failed-task" : "retry-task";
      return {
        id,
        task_id: id,
        status: "queued",
        pricing_contract: "billing_quote",
        billing: {
          operation_id: `operation-${id}`,
          idempotency_key: key,
          status: "reserved",
          requested_credits: 100,
          held_credits: 100,
          settled_credits: 0,
          released_credits: 0
        }
      };
    });
    (estimateVideo as Mock).mockResolvedValueOnce(billedQuote("fresh-video-token"));

    const view = render(
      <VideoTasksProvider>
        <RetryPricedHarness />
      </VideoTasksProvider>
    );
    await act(async () => view.getByText("create-priced").click());
    await waitFor(() => expect(view.getByTestId("retry-status")).toHaveTextContent("failed"));
    expect(estimateVideo).not.toHaveBeenCalled();

    await act(async () => view.getByText("retry-priced").click());
    await waitFor(() => expect(view.getByTestId("retry-result")).toHaveTextContent("retry-task"));
    expect(estimateVideo).toHaveBeenCalledTimes(1);
    expect(createVideo).toHaveBeenLastCalledWith(
      expect.objectContaining({ topic: "retry" }),
      { quote_token: "fresh-video-token", idempotency_key: freshKey }
    );
  });

  it("never auto-reuses an expired quote returned for retry", async () => {
    (createVideo as Mock).mockResolvedValueOnce({
      id: "failed-task",
      task_id: "failed-task",
      status: "queued",
      pricing_contract: "billing_quote",
      billing: {
        operation_id: "operation-failed",
        idempotency_key: oldConfirmation.idempotency_key,
        status: "reserved",
        requested_credits: 100,
        held_credits: 100,
        settled_credits: 0,
        released_credits: 0
      }
    });
    (estimateVideo as Mock).mockResolvedValueOnce(billedQuote("expired-token", "2000-01-01T00:00:00Z"));

    const view = render(
      <VideoTasksProvider>
        <RetryPricedHarness />
      </VideoTasksProvider>
    );
    await act(async () => view.getByText("create-priced").click());
    await waitFor(() => expect(view.getByTestId("retry-status")).toHaveTextContent("failed"));
    await act(async () => view.getByText("retry-priced").click());
    await waitFor(() => expect(view.getByTestId("retry-error")).toHaveTextContent("QUOTE_EXPIRED"));
    expect(createVideo).toHaveBeenCalledTimes(1);
  });
});
