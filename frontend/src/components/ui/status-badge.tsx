import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type TaskStatus = "running" | "done" | "queued" | "failed";

const styles: Record<TaskStatus, string> = {
  running: "bg-run-bg text-run-fg",
  done: "bg-success-bg text-success-fg",
  queued: "bg-queue-bg text-queue-fg",
  failed: "bg-error-bg text-error-fg"
};

export function StatusBadge({
  status,
  children,
  className
}: {
  status: TaskStatus;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex flex-none items-center rounded-badge px-3 py-1.5 text-xs font-medium",
        styles[status],
        className
      )}
    >
      {children}
    </span>
  );
}
