"use client";

import { AlertTriangle, Check, Clapperboard, Clock, ExternalLink, type LucideIcon } from "lucide-react";

import { Card, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/ui/status-badge";
import { cn } from "@/lib/utils";
import { useVideoTasks, type UiStatus } from "@/lib/videos/tasks-context";

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

export function TaskList() {
  const { tasks } = useVideoTasks();

  return (
    <Card animateIn>
      <CardTitle className="mb-3.5">生成任务</CardTitle>

      {tasks.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
          <Clapperboard size={28} strokeWidth={1.6} className="text-ink-faint" />
          <p className="text-[13px] text-ink-soft">暂无任务，输入主题开始生成。</p>
        </div>
      ) : (
        <div>
          {tasks.map((task) => {
            const Icon = thumbIcon[task.status];
            const isHttp = task.videoUrl?.startsWith("http");
            return (
              <div
                key={task.taskId}
                className="flex items-center gap-3.5 border-b border-track px-2 py-3.5 last:border-none"
              >
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
                  {task.status === "failed" && task.error ? (
                    <p className="mt-1 truncate text-[12px] text-error-fg" title={task.error}>
                      {task.error}
                    </p>
                  ) : task.status === "done" && task.videoUrl ? (
                    isHttp ? (
                      <a
                        href={task.videoUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-1 inline-flex items-center gap-1 text-[12px] text-gold-deep hover:underline"
                      >
                        查看成片 <ExternalLink size={12} strokeWidth={2} />
                      </a>
                    ) : (
                      <p className="mt-1 truncate text-[12px] text-ink-faint" title={task.videoUrl}>
                        成片已生成
                      </p>
                    )
                  ) : (
                    <Progress value={task.progress} className="mt-2.5 h-[5px]" />
                  )}
                </div>

                <StatusBadge status={task.status}>{task.statusLabel}</StatusBadge>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}
