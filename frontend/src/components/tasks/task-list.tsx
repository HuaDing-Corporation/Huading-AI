"use client";

import { Clapperboard } from "lucide-react";
import { useRouter } from "next/navigation";

import { Card, CardTitle } from "@/components/ui/card";
import { TaskCard } from "@/components/tasks/task-card";
import { copy } from "@/lib/copy";
import { useVideoTasks } from "@/lib/videos/tasks-context";

/** Live "生成任务" panel — shows only the 2 most recent in-flight/just-finished
 *  tasks; older ones live in the 历史生成 module below. */
export function TaskList() {
  const { tasks, refreshTask, retryTask } = useVideoTasks();
  const router = useRouter();
  const recent = tasks.slice(0, 2);

  return (
    <Card animateIn className="flex flex-col">
      <CardTitle className="mb-3.5">{copy.tasks.title}</CardTitle>

      {tasks.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 py-16 text-center">
          <Clapperboard size={28} strokeWidth={1.6} className="text-ink-faint" />
          <p className="text-[13px] text-ink-soft">{copy.tasks.empty}</p>
        </div>
      ) : (
        <div>
          {recent.map((task) => (
            <TaskCard
              key={task.taskId}
              task={task}
              onOpen={(id) => router.push(`/videos/${id}`)}
              onRetry={(id) => {
                // TaskCard only shows retry for retryable tasks; this catch is a
                // defensive guard so a missing stored request can't become an
                // unhandled rejection (P2-1).
                void retryTask(id).catch(() => undefined);
              }}
              onUrlError={(id) => void refreshTask(id)}
            />
          ))}
          {tasks.length > recent.length ? (
            <p className="mt-3 text-center text-[12px] text-ink-faint">{copy.tasks.moreInHistory}</p>
          ) : null}
        </div>
      )}
    </Card>
  );
}
