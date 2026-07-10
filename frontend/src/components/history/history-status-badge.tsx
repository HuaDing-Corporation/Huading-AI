import { StatusBadge, type TaskStatus } from "@/components/ui/status-badge";
import { copy } from "@/lib/copy";

/** 归一状态 → 徽标（partial_failed/failed 走 error 态醒目；completed/ready 走 done 态）。 */
function statusMeta(status: string): { tone: TaskStatus; label: string } {
  switch (status) {
    case "completed":
      return { tone: "done", label: copy.historyImages.statusCompleted };
    case "ready":
      return { tone: "done", label: copy.historyImages.statusReady };
    case "partial_failed":
      return { tone: "failed", label: copy.historyImages.statusPartial };
    case "failed":
      return { tone: "failed", label: copy.historyImages.statusFailed };
    default:
      return { tone: "done", label: status };
  }
}

export function HistoryStatusBadge({ status }: { status: string }) {
  const { tone, label } = statusMeta(status);
  return (
    <StatusBadge status={tone} className="px-2 py-0.5 text-[11px]">
      {label}
    </StatusBadge>
  );
}
