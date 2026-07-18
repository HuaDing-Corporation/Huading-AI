"use client";

import { useCallback } from "react";
import { Clapperboard } from "lucide-react";
import { useRouter } from "next/navigation";

import { Card, CardTitle } from "@/components/ui/card";
import { TaskCard } from "@/components/tasks/task-card";
import { copy } from "@/lib/copy";
import { useMediaUrlRefreshScope } from "@/lib/media/use-media-url-refresh";
import { useVideoTasks } from "@/lib/videos/tasks-context";

/** Live "生成任务" panel — shows only the 2 most recent in-flight/just-finished
 *  tasks; older ones live in the 历史生成 module below. */
export function TaskList() {
  const { tasks, refreshTask, retryTask } = useVideoTasks();
  const router = useRouter();
  const recent = tasks.slice(0, 2);

  // 🔴 FIX1：这里是那个**反例** —— `refreshTask(taskId)` 只刷**这一个** task（tasks-context:146 走
  // GET /videos/{id}）。N 张卡 = N 个**不同资源**，N 次请求是**必要的**，不是浪费。故按 taskId
  // `forKey` 分区，每张卡各持一份预算 —— 若在这里也合流成一次，就只有一张卡被救回来、其余永远黑着。
  //
  // 同一个 TaskCard 在 generation-history 里却要全列表共用一份（那边一次 refetch 刷全部）——
  // **作用域只有调用方知道**，这正是预算不能放在 TaskCard 里的原因。
  const refresh = useMediaUrlRefreshScope(useCallback((taskId: string) => refreshTask(taskId), [refreshTask]));

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
              refresh={refresh.forKey(task.taskId)}
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
