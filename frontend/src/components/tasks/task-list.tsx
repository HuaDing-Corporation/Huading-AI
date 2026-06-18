"use client";

import { Clapperboard } from "lucide-react";
import { useRouter } from "next/navigation";

import { Card, CardTitle } from "@/components/ui/card";
import { TaskCard } from "@/components/tasks/task-card";
import { copy } from "@/lib/copy";
import { useVideoTasks } from "@/lib/videos/tasks-context";

export function TaskList() {
  const { tasks, refreshTask, retryTask } = useVideoTasks();
  const router = useRouter();

  return (
    <Card animateIn className="flex flex-col">
      <CardTitle className="mb-3.5">生成任务</CardTitle>

      {tasks.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 py-16 text-center">
          <Clapperboard size={28} strokeWidth={1.6} className="text-ink-faint" />
          <p className="text-[13px] text-ink-soft">{copy.tasks.empty}</p>
        </div>
      ) : (
        <div>
          {tasks.map((task) => (
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
        </div>
      )}
    </Card>
  );
}
