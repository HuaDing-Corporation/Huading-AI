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
import { createVideo, getVideoStatus, streamVideoEvents } from "@/lib/api/videos";
import type { CreateVideoRequest, VideoTaskStatus } from "@/lib/api/types";

export type UiStatus = "running" | "done" | "queued" | "failed";

export interface TrackedTask {
  taskId: string;
  topic: string;
  status: UiStatus;
  progress: number; // 0..100
  statusLabel: string;
  videoUrl?: string | null;
  error?: string | null;
}

interface TasksContextValue {
  tasks: TrackedTask[];
  /** Submit a video; resolves to the task_id or throws an ApiError. */
  createAndTrack: (req: CreateVideoRequest, topic: string) => Promise<string>;
}

const TasksContext = createContext<TasksContextValue | null>(null);

function mapStatus(status: string, pct: number): { ui: UiStatus; label: string } {
  switch (status) {
    case "SUCCESS":
      return { ui: "done", label: "已完成" };
    case "FAILURE":
      return { ui: "failed", label: "失败" };
    case "PROGRESS":
    case "STARTED":
      return { ui: "running", label: `生成中 ${pct}%` };
    default:
      return { ui: "queued", label: "排队中" };
  }
}

export function VideoTasksProvider({ children }: { children: ReactNode }) {
  const [tasks, setTasks] = useState<TrackedTask[]>([]);
  const controllers = useRef<Map<string, AbortController>>(new Map());

  const patch = useCallback((taskId: string, next: Partial<TrackedTask>) => {
    setTasks((prev) => prev.map((t) => (t.taskId === taskId ? { ...t, ...next } : t)));
  }, []);

  const applyEvent = useCallback(
    (taskId: string, event: VideoTaskStatus) => {
      if (event.stage === "sse_timeout") return; // informational keep-alive
      const pct = Math.round((event.progress ?? 0) * 100);
      const { ui, label } = mapStatus(event.status, pct);
      patch(taskId, {
        status: ui,
        progress: pct,
        statusLabel: label,
        videoUrl: event.video_url ?? undefined,
        error: event.error ?? undefined
      });
    },
    [patch]
  );

  const pollFallback = useCallback(
    async (taskId: string, signal: AbortSignal) => {
      while (!signal.aborted) {
        try {
          const status = await getVideoStatus(taskId);
          applyEvent(taskId, status);
          if (status.status === "SUCCESS" || status.status === "FAILURE") return;
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) return;
          patch(taskId, { status: "failed", statusLabel: "失败", error: "进度获取失败" });
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
    },
    [applyEvent, patch]
  );

  const subscribe = useCallback(
    (taskId: string) => {
      const controller = new AbortController();
      controllers.current.set(taskId, controller);
      streamVideoEvents(taskId, (event) => applyEvent(taskId, event), controller.signal)
        .then(async () => {
          // Stream ended (terminal or SSE cap) — reconcile the final state.
          if (controller.signal.aborted) return;
          try {
            applyEvent(taskId, await getVideoStatus(taskId));
          } catch {
            // ignore
          }
        })
        .catch((err) => {
          if (controller.signal.aborted) return;
          if (err instanceof ApiError && err.status === 401) return;
          // SSE unavailable → fall back to polling.
          void pollFallback(taskId, controller.signal);
        })
        .finally(() => controllers.current.delete(taskId));
    },
    [applyEvent, pollFallback]
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

  useEffect(() => {
    const active = controllers.current;
    return () => {
      active.forEach((controller) => controller.abort());
      active.clear();
    };
  }, []);

  return <TasksContext.Provider value={{ tasks, createAndTrack }}>{children}</TasksContext.Provider>;
}

export function useVideoTasks(): TasksContextValue {
  const ctx = useContext(TasksContext);
  if (!ctx) throw new Error("useVideoTasks must be used within a VideoTasksProvider");
  return ctx;
}
