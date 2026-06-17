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

import { ApiError } from "@/lib/api/client";
import { createVideo, getVideo, listVideos, streamVideoEvents } from "@/lib/api/videos";
import type { CreateVideoRequest, VideoEvent, VideoRead } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-context";
import { eventToProgress, fromVideoRead, TERMINAL, type TrackedTask } from "@/lib/sse/progress-mapping";

export type { TrackedTask, UiStatus } from "@/lib/sse/progress-mapping";

interface TasksContextValue {
  tasks: TrackedTask[];
  /** Submit a video; resolves to the task_id or throws an ApiError. */
  createAndTrack: (req: CreateVideoRequest, topic: string) => Promise<string>;
  /** Re-fetch one video (e.g. to refresh an expired presigned playback URL). */
  refreshTask: (taskId: string) => Promise<void>;
}

const TasksContext = createContext<TasksContextValue | null>(null);

export function VideoTasksProvider({ children }: { children: ReactNode }) {
  const [tasks, setTasks] = useState<TrackedTask[]>([]);
  const controllers = useRef<Map<string, AbortController>>(new Map());
  const hydratedRef = useRef(false);
  const { session } = useAuth();

  const patch = useCallback((taskId: string, next: Partial<TrackedTask>) => {
    setTasks((prev) => prev.map((t) => (t.taskId === taskId ? { ...t, ...next } : t)));
  }, []);

  /** Authoritative update from a VideoRead (carries playback/download URLs). */
  const applyRead = useCallback(
    (read: VideoRead) => {
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
      if (next) patch(taskId, next);
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
          if (TERMINAL.includes(read.status)) return;
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) return;
          patch(taskId, { status: "failed", statusLabel: "失败", error: "进度获取失败" });
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
    },
    [applyRead, patch]
  );

  const subscribe = useCallback(
    (taskId: string) => {
      if (controllers.current.has(taskId)) return;
      const controller = new AbortController();
      controllers.current.set(taskId, controller);
      streamVideoEvents(taskId, (event) => applyEvent(taskId, event), controller.signal)
        .then(async () => {
          // Stream ended (terminal or SSE cap) — reconcile the authoritative
          // record so we pick up playback_url/download_url (avoids done->queued).
          if (controller.signal.aborted) return;
          await refreshTask(taskId);
        })
        .catch((err) => {
          if (controller.signal.aborted) return;
          if (err instanceof ApiError && err.status === 401) return;
          // SSE unavailable (e.g. hydrated task from another process) -> poll.
          void pollFallback(taskId, controller.signal);
        })
        .finally(() => controllers.current.delete(taskId));
    },
    [applyEvent, pollFallback, refreshTask]
  );

  const createAndTrack = useCallback(
    async (req: CreateVideoRequest, topic: string): Promise<string> => {
      const accepted = await createVideo(req);
      setTasks((prev) => [
        {
          taskId: accepted.task_id,
          topic,
          status: "queued",
          progress: 0,
          statusLabel: "排队中"
        },
        ...prev
      ]);
      subscribe(accepted.task_id);
      return accepted.task_id;
    },
    [subscribe]
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

  // On logout, abort live streams and clear the list so the next user starts
  // clean and a re-login re-hydrates from scratch.
  useEffect(() => {
    if (session) return;
    hydratedRef.current = false;
    const active = controllers.current;
    active.forEach((controller) => controller.abort());
    active.clear();
    setTasks([]);
  }, [session]);

  useEffect(() => {
    const active = controllers.current;
    return () => {
      active.forEach((controller) => controller.abort());
      active.clear();
    };
  }, []);

  return (
    <TasksContext.Provider value={{ tasks, createAndTrack, refreshTask }}>
      {children}
    </TasksContext.Provider>
  );
}

export function useVideoTasks(): TasksContextValue {
  const ctx = useContext(TasksContext);
  if (!ctx) throw new Error("useVideoTasks must be used within a VideoTasksProvider");
  return ctx;
}
