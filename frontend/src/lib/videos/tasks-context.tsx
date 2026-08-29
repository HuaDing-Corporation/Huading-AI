"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode
} from "react";

import { getBillingOperation } from "@/lib/api/billing";
import { ApiError } from "@/lib/api/client";
import { createVideo, estimateVideo, getVideo, listVideos, streamVideoEvents } from "@/lib/api/videos";
import type {
  BillingConfirmation,
  BillingOperationLookupFor,
  CreateVideoRequest,
  VideoAcceptedContract,
  VideoDetail,
  VideoEstimateContract,
  VideoEvent,
  VideoListItem
} from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-context";
import { HARD_CAP_MS, POLL_MS, STALL_MS } from "@/lib/sse/constants";
import { eventToProgress, fromVideoRead, labelFor, mapSseStatus, TERMINAL, type TrackedTask } from "@/lib/sse/progress-mapping";

export type { TrackedTask, UiStatus } from "@/lib/sse/progress-mapping";

export type VideoPricingAttempt =
  | {
      estimate: Extract<VideoEstimateContract, { pricing_contract: "billing_quote" }>;
      confirmation: BillingConfirmation;
    }
  | {
      estimate: Extract<
        VideoEstimateContract,
        { pricing_contract: "legacy_estimate" | "deferred_unpriced" }
      >;
      confirmation?: never;
    };

interface CreateAndTrack {
  (req: CreateVideoRequest, topic: string): Promise<string>;
  (
    req: CreateVideoRequest,
    topic: string,
    pricing: VideoPricingAttempt
  ): Promise<VideoAcceptedContract>;
}

interface TasksContextValue {
  tasks: TrackedTask[];
  /** Submit a video; resolves to the task id or throws an ApiError. */
  createAndTrack: CreateAndTrack;
  /** Re-fetch one video (e.g. to refresh an expired presigned playback URL). */
  refreshTask: (taskId: string) => Promise<void>;
  /**
   * Track an already-created task by id — e.g. POST /ecom-images/cutout(/batch)
   * returns task_id(s) for backend-created photo tasks (kind=ecom_cutout). Adds the
   * task and subscribes to its SSE/poll progress without re-creating via createVideo.
   */
  trackExisting: (taskId: string, topic: string, mode?: string | null, applyVisibleLabel?: boolean) => void;
  /**
   * Re-submit the original request for a failed task, creating a new task.
   * Resolves to the new task id or throws if the original request is unknown.
   */
  retryTask: (taskId: string) => Promise<string>;
}

const TasksContext = createContext<TasksContextValue | null>(null);

// Watchdog cadence: tick at most every second in production (STALL_MS is large),
// but follow a tiny STALL_MS in tests so a forced stall is detected promptly.
const WATCHDOG_TICK_MS = Math.min(1000, STALL_MS);
const VIDEO_RECOVERY_LOOKUP_ATTEMPTS = 3;
const VIDEO_RECOVERY_LOOKUP_INTERVAL_MS = 1_000;

interface TaskMeta {
  lastProgressAt: number;
  queuedAt: number;
  lastPct: number;
  lastStep: string | null;
}

interface StoredRequest {
  req: CreateVideoRequest;
  topic: string;
  pricing_contract: VideoAcceptedContract["pricing_contract"];
  operation: "video_create" | null;
  confirmation: BillingConfirmation | null;
  estimate: VideoEstimateContract | null;
}

function isUnknownVideoPost(error: unknown): boolean {
  return (
    !(error instanceof ApiError) ||
    error.status === 0 ||
    error.status === 502 ||
    error.status === 503 ||
    error.status === 504 ||
    error.code === "INVALID_VIDEO_ACCEPTED_CONTRACT"
  );
}

function acceptedFromVideoLookup(
  lookup: BillingOperationLookupFor<"video_create">
): VideoAcceptedContract | null {
  if (lookup.state === "completed" && lookup.completion_kind === "failed") {
    throw new ApiError(
      "视频生成未完成，冻结积分已释放。",
      lookup.failure.code,
      lookup.failure.original_http_status,
      lookup.failure.detail
    );
  }
  if (
    lookup.state === "completed" &&
    lookup.completion_kind === "succeeded" &&
    lookup.result_type === "video_task"
  ) {
    return {
      id: lookup.result.task_id,
      task_id: lookup.result.task_id,
      status: lookup.result.status,
      pricing_contract: "billing_quote",
      billing: lookup.billing
    };
  }
  if (
    lookup.state === "in_progress" &&
    lookup.result_type === "video_task" &&
    lookup.resource
  ) {
    return {
      id: lookup.resource.task_id,
      task_id: lookup.resource.task_id,
      status: lookup.resource.status,
      pricing_contract: "billing_quote",
      billing: lookup.billing
    };
  }
  return null;
}

function waitForNextVideoRecoveryLookup(): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, VIDEO_RECOVERY_LOOKUP_INTERVAL_MS);
  });
}

async function recoverUnknownBilledVideo(
  confirmation: BillingConfirmation
): Promise<VideoAcceptedContract> {
  for (let attempt = 0; attempt < VIDEO_RECOVERY_LOOKUP_ATTEMPTS; attempt += 1) {
    if (attempt > 0) await waitForNextVideoRecoveryLookup();

    let lookup: BillingOperationLookupFor<"video_create">;
    try {
      lookup = await getBillingOperation("video_create", confirmation.idempotency_key);
    } catch {
      continue;
    }

    const recovered = acceptedFromVideoLookup(lookup);
    if (recovered) return recovered;
  }

  throw new ApiError("计费结果确认中", "BILLING_RESULT_PENDING", 0);
}

export function VideoTasksProvider({ children }: { children: ReactNode }) {
  const [tasks, setTasks] = useState<TrackedTask[]>([]);
  const controllers = useRef<Map<string, AbortController>>(new Map());
  const timers = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());
  const meta = useRef<Map<string, TaskMeta>>(new Map());
  /** Original request + pricing attempt keyed by taskId — used for recovery/retry. */
  const requests = useRef<Map<string, StoredRequest>>(new Map());
  const hydratedRef = useRef(false);
  const { session } = useAuth();

  const patch = useCallback((taskId: string, next: Partial<TrackedTask>) => {
    setTasks((prev) => prev.map((t) => (t.taskId === taskId ? { ...t, ...next } : t)));
  }, []);

  // Stop the per-task stall/cap watchdog and forget its progress bookkeeping.
  const stopWatchdog = useCallback((taskId: string) => {
    const tm = timers.current.get(taskId);
    if (tm) clearInterval(tm);
    timers.current.delete(taskId);
    meta.current.delete(taskId);
  }, []);

  // Force a local `failed` even if the backend never emits one: abort the live
  // stream/poll, mark the task retry-able, and tear down the watchdog (timeout-1).
  const failTask = useCallback(
    (taskId: string, message: string) => {
      patch(taskId, { status: "failed", statusLabel: "失败", error: message });
      controllers.current.get(taskId)?.abort();
      controllers.current.delete(taskId);
      stopWatchdog(taskId);
    },
    [patch, stopWatchdog]
  );

  // Start a per-task watchdog the moment a task goes in-flight. GEN-TIMEOUT-1500-UI-0001：
  // 看门狗是**后端失联的最后一道兜底**（worker 崩 / SSE 断 / 任务卡死），**不是生成超时的判定者**——
  // STALL_MS/HARD_CAP_MS 都晚于后端权威超时（BE 1500s / nginx SSE 1800s），故正常生成期间它永不抢跑。
  // 只有当既收不到 SSE 进度、也拉不到 poll 更新、且超出这些兜底窗口时才强制本地 `failed`。
  // 旧值（120s/900s）会在图片生成（无逐轮进度回调）时于 120s 误杀后端还在跑的任务——本包根因修复。
  const startWatchdog = useCallback(
    (taskId: string) => {
      if (timers.current.has(taskId)) return;
      const now = Date.now();
      meta.current.set(taskId, { lastProgressAt: now, queuedAt: now, lastPct: -1, lastStep: null });
      const tm = setInterval(() => {
        const m = meta.current.get(taskId);
        if (!m) return;
        const t = Date.now();
        if (t - m.queuedAt > HARD_CAP_MS || t - m.lastProgressAt > STALL_MS) {
          failTask(taskId, "生成超时，请重试");
        }
      }, WATCHDOG_TICK_MS);
      timers.current.set(taskId, tm);
    },
    [failTask]
  );

  /** Authoritative update from a VideoDetail/VideoListItem (carries URLs). */
  const applyRead = useCallback(
    (read: VideoDetail | VideoListItem) => {
      const mapped = fromVideoRead(read);
      setTasks((prev) =>
        prev.some((t) => t.taskId === mapped.taskId)
          ? prev.map((t) => (t.taskId === mapped.taskId ? { ...t, ...mapped } : t))
          : [mapped, ...prev]
      );
    },
    []
  );

  /** Live SSE progress (status/percent only; URLs come from the reconcile). */
  const applyEvent = useCallback(
    (taskId: string, event: VideoEvent) => {
      const next = eventToProgress(event);
      if (!next) return;
      // Refresh the no-progress clock only on real forward motion: a higher
      // percent or a step change. Repeated identical frames keep the clock
      // ticking toward STALL_MS so a wedged task still trips the watchdog.
      const m = meta.current.get(taskId);
      const beat = event.heartbeat_at != null;
      const now = Date.now();
      if (m) {
        const step = event.step ?? null;
        const advanced = next.progress > m.lastPct || step !== m.lastStep;
        // 🔴 只有"真实前进"能动 lastPct/lastStep —— 心跳**碰不到这两个字段**。拆成两段（而不是 if/else）
        // 是为了让这条不变量从**结构**上看得见，而不是只写在注释里：让心跳推进 lastPct 等于把假进度写进
        // 状态，且之后真实前进帧会被误判成"没前进"、失去重置能力。
        if (advanced) {
          m.lastPct = Math.max(m.lastPct, next.progress);
          m.lastStep = step;
        }
        // GEN-HEARTBEAT-UI-0001：心跳是与"真实前进"**并列的第二条**重置条件。
        // BE 的心跳不改 progress、不改 step（冻结 §四），只认上面那条判据会把它整帧忽略 → 27 分钟死寂照旧。
        // 看门狗并没被废：心跳一停，时钟照常走向 STALL_MS。
        if (advanced || beat) m.lastProgressAt = now;
      }
      patch(taskId, beat ? { ...next, heartbeatAt: now } : next);
    },
    [patch]
  );

  const refreshTask = useCallback(
    async (taskId: string) => {
      try {
        applyRead(await getVideo(taskId));
      } catch {
        // leave the current state in place
      }
    },
    [applyRead]
  );

  const pollFallback = useCallback(
    async (taskId: string, signal: AbortSignal) => {
      while (!signal.aborted) {
        try {
          const read = await getVideo(taskId);
          applyRead(read);
          if (TERMINAL.includes(read.status)) {
            stopWatchdog(taskId);
            return;
          }
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) {
            stopWatchdog(taskId);
            return;
          }
          patch(taskId, { status: "failed", statusLabel: "失败", error: "进度获取失败" });
          stopWatchdog(taskId);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      }
    },
    [applyRead, patch, stopWatchdog]
  );

  const subscribe = useCallback(
    (taskId: string) => {
      if (controllers.current.has(taskId)) return;
      const controller = new AbortController();
      controllers.current.set(taskId, controller);
      startWatchdog(taskId);
      streamVideoEvents(taskId, (event) => applyEvent(taskId, event), controller.signal)
        .then(async () => {
          // Stream ended (terminal or SSE cap) — reconcile the authoritative
          // record so we pick up playback_url/download_url (avoids done->queued).
          if (controller.signal.aborted) return;
          await refreshTask(taskId);
          stopWatchdog(taskId);
        })
        .catch((err) => {
          if (controller.signal.aborted) return;
          if (err instanceof ApiError && err.status === 401) {
            stopWatchdog(taskId);
            return;
          }
          // SSE unavailable (e.g. hydrated task from another process) -> poll.
          void pollFallback(taskId, controller.signal);
        })
        .finally(() => controllers.current.delete(taskId));
    },
    [applyEvent, pollFallback, refreshTask, startWatchdog, stopWatchdog]
  );

  const registerAccepted = useCallback((
    accepted: VideoAcceptedContract,
    req: CreateVideoRequest,
    topic: string,
    pricing?: VideoPricingAttempt
  ) => {
    const status = mapSseStatus(accepted.status);
    requests.current.set(accepted.id, {
      req,
      topic,
      pricing_contract: accepted.pricing_contract,
      operation: accepted.pricing_contract === "billing_quote" ? "video_create" : null,
      confirmation: pricing?.confirmation ?? null,
      estimate: pricing?.estimate ?? null
    });
    setTasks((prev) => [
      {
        taskId: accepted.id,
        topic,
        mode: req.video_mode ?? null,
        status,
        progress: status === "done" ? 100 : 0,
        statusLabel: labelFor(status, status === "done" ? 100 : 0),
        applyVisibleLabel: req.apply_visible_label ?? false,
        startedAt: Date.now(),
        retryable: true
      },
      ...prev.filter((task) => task.taskId !== accepted.id)
    ]);
    if (!TERMINAL.includes(status)) subscribe(accepted.id);
  }, [subscribe]);

  const createAndTrack = useCallback(
    async (
      req: CreateVideoRequest,
      topic: string,
      pricing?: VideoPricingAttempt
    ): Promise<string | VideoAcceptedContract> => {
      const confirmation =
        pricing?.estimate.pricing_contract === "billing_quote" ? pricing.confirmation : undefined;
      if (
        pricing?.estimate.pricing_contract === "billing_quote" &&
        Date.parse(pricing.estimate.expires_at) <= Date.now()
      ) {
        throw new ApiError("报价已过期，请重新获取价格。", "QUOTE_EXPIRED", 409);
      }

      let accepted: VideoAcceptedContract;
      try {
        accepted = await createVideo(req, confirmation);
      } catch (caught) {
        if (!confirmation || !isUnknownVideoPost(caught)) throw caught;
        accepted = await recoverUnknownBilledVideo(confirmation);
      }

      if (pricing && accepted.pricing_contract !== pricing.estimate.pricing_contract) {
        throw new ApiError(
          "视频创建响应与已确认的定价分支不一致。",
          "INVALID_VIDEO_ACCEPTED_CONTRACT",
          502
        );
      }
      registerAccepted(accepted, req, topic, pricing);
      return pricing ? accepted : accepted.id;
    },
    [registerAccepted]
  ) as CreateAndTrack;

  // Track a backend-created task (cutout/batch) by id — reuse subscribe()'s full
  // SSE/poll/watchdog/reconcile machinery; product lands in TaskList + image history.
  const trackExisting = useCallback(
    (taskId: string, topic: string, mode?: string | null, applyVisibleLabel?: boolean): void => {
      setTasks((prev) =>
        prev.some((t) => t.taskId === taskId)
          ? prev
          : [
              {
                taskId,
                topic,
                mode: mode ?? null,
                status: "queued",
                progress: 0,
                statusLabel: "排队中",
                applyVisibleLabel: applyVisibleLabel ?? false, // ecom session 卡即时徽标（LABEL-TOGGLE-UI-0001）
                startedAt: Date.now(), // 同上：诚实计时基准（GEN-HEARTBEAT-UI-0001）
                retryable: false // 无 stored request；重试由各工具自行重新提交
              },
              ...prev
            ]
      );
      subscribe(taskId);
    },
    [subscribe]
  );

  /**
   * Re-submit the stored original request for a failed task.
   * Looks up the request by `taskId`; if none is found the promise rejects.
   * The retry creates a brand-new task (new id) and the old card stays visible
   * until a future list refresh replaces it.
   */
  const retryTask = useCallback(
    async (taskId: string): Promise<string> => {
      const stored = requests.current.get(taskId);
      if (!stored) {
        throw new Error(`No stored request for task ${taskId}`);
      }
      const current = tasks.find((task) => task.taskId === taskId);
      if (!current || current.status !== "failed") {
        throw new ApiError("只有失败任务可以重新尝试。", "VIDEO_RETRY_NOT_ALLOWED", 409);
      }

      // Explicit retry always starts a new pricing attempt. The stored token/key
      // remain available only for recovering the original POST and are never reused.
      const freshEstimate = await estimateVideo(stored.req);
      let pricing: VideoPricingAttempt;
      if (freshEstimate.pricing_contract === "billing_quote") {
        if (Date.parse(freshEstimate.expires_at) <= Date.now()) {
          throw new ApiError("报价已过期，请重新获取价格。", "QUOTE_EXPIRED", 409);
        }
        pricing = {
          estimate: freshEstimate,
          confirmation: {
            quote_token: freshEstimate.quote_token,
            idempotency_key: globalThis.crypto.randomUUID()
          }
        };
      } else {
        pricing = { estimate: freshEstimate };
      }
      const accepted = await createAndTrack(stored.req, stored.topic, pricing);
      return accepted.id;
    },
    [createAndTrack, tasks]
  );

  // Hydrate the list once we have a session (B4) and resume in-flight tasks.
  // Gating on `session` prevents a tokenless GET /videos from racing
  // AuthProvider's hydration (which 401s and used to wipe the session).
  useEffect(() => {
    if (!session) return;
    if (hydratedRef.current) return;
    hydratedRef.current = true;
    let cancelled = false;
    (async () => {
      try {
        const items = await listVideos();
        if (cancelled) return;
        const mapped = items.map(fromVideoRead);
        setTasks((prev) => {
          const known = new Set(mapped.map((t) => t.taskId));
          return [...mapped, ...prev.filter((t) => !known.has(t.taskId))];
        });
        for (const task of mapped) {
          if (!TERMINAL.includes(task.status)) subscribe(task.taskId);
        }
      } catch {
        // empty list / unauthenticated -> show empty state
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [session, subscribe]);

  // On logout, abort live streams + watchdogs and clear the list so the next
  // user starts clean and a re-login re-hydrates from scratch.
  useEffect(() => {
    if (session) return;
    hydratedRef.current = false;
    const active = controllers.current;
    active.forEach((controller) => controller.abort());
    active.clear();
    timers.current.forEach((tm) => clearInterval(tm));
    timers.current.clear();
    meta.current.clear();
    requests.current.clear();
    setTasks([]);
  }, [session]);

  useEffect(() => {
    const activeControllers = controllers.current;
    const activeTimers = timers.current;
    const activeMeta = meta.current;
    const activeRequests = requests.current;
    return () => {
      activeControllers.forEach((controller) => controller.abort());
      activeControllers.clear();
      activeTimers.forEach((tm) => clearInterval(tm));
      activeTimers.clear();
      activeMeta.clear();
      activeRequests.clear();
    };
  }, []);

  return (
    <TasksContext.Provider value={{ tasks, createAndTrack, trackExisting, refreshTask, retryTask }}>
      {children}
    </TasksContext.Provider>
  );
}

export function useVideoTasks(): TasksContextValue {
  const ctx = useContext(TasksContext);
  if (!ctx) throw new Error("useVideoTasks must be used within a VideoTasksProvider");
  return ctx;
}
