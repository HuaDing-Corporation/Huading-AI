"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { getVideo, streamVideoEvents } from "@/lib/api/videos";
import { labelFor, mapSseStatus, TERMINAL, type UiStatus } from "@/lib/sse/progress-mapping";

export interface TaskProgress {
  status: UiStatus;
  progress: number; // 0..100
  statusLabel: string;
  error?: string | null;
}

const INITIAL: TaskProgress = { status: "queued", progress: 0, statusLabel: labelFor("queued", 0) };

/**
 * Single-task progress: subscribe to the SSE stream, fall back to polling if SSE
 * is unavailable, and reconcile the authoritative record on terminal. Returns the
 * live status/percent for one task.
 */
export function useTaskProgress(taskId: string | undefined): TaskProgress {
  const [state, setState] = useState<TaskProgress>(INITIAL);

  useEffect(() => {
    if (!taskId) return;
    const controller = new AbortController();

    const apply = (status: UiStatus, pct: number, error?: string | null) => {
      setState({
        status,
        progress: status === "done" ? 100 : pct,
        statusLabel: labelFor(status, pct),
        error: error ?? undefined
      });
    };

    const poll = async () => {
      while (!controller.signal.aborted) {
        try {
          const read = await getVideo(taskId);
          apply(read.status, read.progress ?? 0, read.error);
          if (TERMINAL.includes(read.status)) return;
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) return;
          return;
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
    };

    streamVideoEvents(
      taskId,
      (event) => {
        if (event.stage === "sse_timeout") return;
        const pct = Math.round((event.progress ?? 0) * 100);
        apply(mapSseStatus(event.status), pct, event.error);
      },
      controller.signal
    )
      .then(async () => {
        if (controller.signal.aborted) return;
        try {
          const read = await getVideo(taskId);
          apply(read.status, read.progress ?? 0, read.error);
        } catch {
          // keep last state
        }
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 401) return;
        void poll();
      });

    return () => controller.abort();
  }, [taskId]);

  return state;
}
