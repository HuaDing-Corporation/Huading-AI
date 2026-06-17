"use client";

import { useRef } from "react";
import {
  AlertTriangle,
  Check,
  Clapperboard,
  Clock,
  Download,
  type LucideIcon
} from "lucide-react";

import { Card, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/ui/status-badge";
import { cn } from "@/lib/utils";
import { useVideoTasks, type TrackedTask, type UiStatus } from "@/lib/videos/tasks-context";

const thumbIcon: Record<UiStatus, LucideIcon> = {
  running: Clapperboard,
  done: Check,
  queued: Clock,
  failed: AlertTriangle
};

const thumbStyle: Record<UiStatus, string> = {
  running: "bg-grad-gold text-ink shadow-thumb",
  done: "bg-grad-done text-ink shadow-thumb-done",
  queued: "bg-track text-ink-faint",
  failed: "bg-error-bg text-error-fg"
};

function TaskPlayer({ task }: { task: TrackedTask }) {
  const { refreshTask } = useVideoTasks();
  const retried = useRef(false);

  // Presigned URLs can expire — refresh the record once on a playback error.
  const onError = () => {
    if (retried.current) return;
    retried.current = true;
    void refreshTask(task.taskId);
  };

  return (
    <div className="mt-3">
      <video
        controls
        preload="metadata"
        poster={task.thumbnailUrl ?? undefined}
        src={task.playbackUrl ?? undefined}
        onError={onError}
        className="max-h-[320px] w-full rounded-field border border-line-gold bg-black/5"
      />
      {task.downloadUrl && (
        <a
          href={task.downloadUrl}
          download
          className="mt-2 inline-flex items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep transition-colors hover:bg-glass-hover"
        >
          <Download size={14} strokeWidth={2} /> 下载 MP4
        </a>
      )}
    </div>
  );
}

export function TaskList() {
  const { tasks } = useVideoTasks();

  return (
    <Card animateIn className="flex flex-col">
      <CardTitle className="mb-3.5">生成任务</CardTitle>

      {tasks.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 py-16 text-center">
          <Clapperboard size={28} strokeWidth={1.6} className="text-ink-faint" />
          <p className="text-[13px] text-ink-soft">暂无任务，输入主题开始生成。</p>
        </div>
      ) : (
        <div>
          {tasks.map((task) => {
            const Icon = thumbIcon[task.status];
            const showPlayer = task.status === "done" && !!task.playbackUrl;
            return (
              <div key={task.taskId} className="border-b border-track py-3.5 last:border-none">
                <div className="flex items-center gap-3.5 px-2">
                  <div
                    className={cn(
                      "flex h-12 w-12 flex-none items-center justify-center rounded-chip",
                      thumbStyle[task.status]
                    )}
                  >
                    <Icon size={20} strokeWidth={1.8} />
                  </div>

                  <div className="min-w-0 flex-1">
                    <b className="block truncate text-sm font-medium text-ink">{task.topic}</b>
                    {task.status === "done" && task.durationSec ? (
                      <span className="text-[12px] text-ink-faint">
                        时长 {Math.round(task.durationSec)} 秒
                      </span>
                    ) : null}
                  </div>

                  <StatusBadge status={task.status}>{task.statusLabel}</StatusBadge>
                </div>

                {showPlayer ? (
                  <div className="px-2">
                    <TaskPlayer task={task} />
                  </div>
                ) : task.status === "failed" ? (
                  task.error ? (
                    <p className="mt-2 px-2 text-[12px] text-error-fg" title={task.error}>
                      {task.error}
                    </p>
                  ) : null
                ) : (
                  <Progress value={task.progress} className="mt-2.5 h-[5px]" />
                )}
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}
