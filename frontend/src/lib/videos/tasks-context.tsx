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
import type { CreateVideoRequest, VideoEvent, VideoRead, VideoStatus } from "@/lib/api/types";

export type UiStatus = VideoStatus; // queued | running | done | failed

export interface TrackedTask {
  taskId: string;
  topic: string;
  status: UiStatus;
  progress: number; // 0..100
  statusLabel: string;
  playbackUrl?: string | null;
  downloadUrl?: string | null;
  thumbnailUrl?: string | null;
  durationSec?: number | null;
  error?: string | null;
}

interface TasksContextValue {
  tasks: TrackedTask[];
  /** Submit a video; resolves to the task_id or throws an ApiError. */
  createAndTrack: (req: CreateVideoRequest, topic: string) => Promise<string>;
  /** Re-fetch one video (e.g. to refresh an expired presigned playback URL). */
  refreshTask: (taskId: string) => Promise<void>;
}

const TasksContext = createContext<TasksContextValue | null>(null);

const _TERMINAL: UiStatus[] = ["done", "failed"];

function labelFor(status: UiStatus, pct: number): string {
  switch (status) {
    case "done":
      return "已完成";
    case "failed":
      return "失败";
    case "running":
      return `生成中 ${pct}%`;
    default:
      return "排队中";
  }
}

/** SSE frame -> UI status (the stream still uses uppercase worker statuses). */
function mapSseStatus(status: string | undefined): UiStatus {
  switch ((status ?? "").toUpperCase()) {
    case "SUCCESS":
    case "DONE":
      return "done";
    case "FAILURE":
    case "FAILED":
      return "failed";
    case "PROGRESS":
    case "STARTED":
    case "RUNNING":
      return "running";
    default:
      return "queued";
  }
}

function fromVideoRead(read: VideoRead): TrackedTask {
  const pct = read.progress ?? 0;
  return {
    taskId: read.id,
    topic: read.title || read.prompt || "未命名视频",
    status: read.status,
    progress: pct,
    statusLabel: labelFor(read.status, pct),
    playbackUrl: read.playback_url ?? null,
    downloadUrl: read.download_url ?? null,
    thumbnailUrl: read.thumbnail_url ?? null,
    durationSec: read.duration_sec ?? null,
    error: read.error ?? null
  };
}

export function VideoTasksProvider({ children }: { children: ReactNode }) {
  const [tasks, setTasks] = useState<TrackedTask[]>([]);
  const controllers = useRef<Map<string, AbortController>>(new Map());
  const hydratedRef = useRef(false);

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
      if (event.stage === "sse_timeout") return; // informational keep-alive
      const pct = Math.round((event.progress ?? 0) * 100);
      const status = mapSseStatus(event.status);
      patch(taskId, {
        status,
        progress: status === "done" ? 100 : pct,
        statusLabel: labelFor(status, pct),
        error: event.error ?? undefined
      });
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
          if (_TERMINAL.includes(read.status)) return;
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

  // Hydrate the list once on mount (B4) and resume any in-flight tasks.
  useEffect(() => {
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
          if (!_TERMINAL.includes(task.status)) subscribe(task.taskId);
        }
      } catch {
        // empty list / unauthenticated -> show empty state
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [subscribe]);

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
