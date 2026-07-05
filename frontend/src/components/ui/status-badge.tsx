import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type TaskStatus = "running" | "done" | "queued" | "failed" | "cancelled";

const styles: Record<TaskStatus, string> = {
  running: "bg-run-bg text-run-fg",
  done: "bg-success-bg text-success-fg",
  queued: "bg-queue-bg text-queue-fg",
  failed: "bg-error-bg text-error-fg",
  cancelled: "bg-track text-ink-faint" // 已取消：中性淡态
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
        // 兜底：未来未知状态也不裸样式（纵深防御，不替代类型覆盖）。
        styles[status] ?? "bg-track text-ink-faint",
        className
      )}
    >
      {children}
    </span>
  );
}
