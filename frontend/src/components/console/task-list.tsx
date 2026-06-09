import { Check, Clapperboard, Clock, type LucideIcon } from "lucide-react";

import { Card, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { StatusBadge, type TaskStatus } from "@/components/ui/status-badge";
import { cn } from "@/lib/utils";
import { tasks } from "@/lib/mock";

const thumbIcon: Record<TaskStatus, LucideIcon> = {
  running: Clapperboard,
  done: Check,
  queued: Clock
};

const thumbStyle: Record<TaskStatus, string> = {
  running: "bg-grad-gold text-ink shadow-thumb",
  done: "bg-grad-done text-ink shadow-thumb-done",
  queued: "bg-track text-ink-faint"
};

export function TaskList() {
  return (
    <Card animateIn>
      <CardTitle className="mb-3.5">生成任务</CardTitle>

      <div>
        {tasks.map((task) => {
          const Icon = thumbIcon[task.status];
          return (
            <div
              key={task.id}
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
                <b className="block truncate text-sm font-medium text-ink">{task.title}</b>
                <Progress value={task.progress} className="mt-2.5 h-[5px]" />
              </div>

              <StatusBadge status={task.status}>{task.statusLabel}</StatusBadge>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
