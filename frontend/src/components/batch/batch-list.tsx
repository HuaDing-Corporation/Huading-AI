"use client";

import { useBatches } from "@/lib/api/hooks";
import type { BatchStatus } from "@/lib/api/types";
import { Card, CardTitle } from "@/components/ui/card";
import { copy } from "@/lib/copy";

export const BATCH_STATUS_LABEL: Record<BatchStatus, string> = {
  running: copy.batch.statusRunning,
  completed: copy.batch.statusCompleted,
  partial_failed: copy.batch.statusPartial,
  failed: copy.batch.statusFailed,
  cancelled: copy.batch.statusCancelled
};
const STATUS_CLASS: Record<BatchStatus, string> = {
  running: "border-line-gold bg-glass-fill text-ink-soft",
  completed: "border-line-sel bg-chip-sel text-gold-deep",
  partial_failed: "border-line-gold bg-glass-fill text-error-fg",
  failed: "border-line-gold bg-error-bg text-error-fg",
  cancelled: "border-line-gold bg-glass-fill text-ink-faint"
};

/** 批次记录列表（BATCH-PROD-UI-0001）——状态 + 进度 x/N；点条目查看详情。轮询由 useBatches 管理。 */
export function BatchList({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, isLoading, isError, refetch } = useBatches();
  const items = data ?? [];

  return (
    <Card animateIn>
      <CardTitle className="mb-[14px]">{copy.batch.listTitle}</CardTitle>
      {isLoading ? (
        <p className="text-[13px] text-ink-soft">{copy.batch.listLoading}</p>
      ) : isError ? (
        <div className="text-[13px] text-error-fg">
          {copy.batch.listError}{" "}
          <button type="button" onClick={() => void refetch()} className="text-gold-deep underline">
            {copy.history.retry}
          </button>
        </div>
      ) : items.length === 0 ? (
        <p className="text-[13px] text-ink-soft">{copy.batch.listEmpty}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((b) => (
            <li key={b.id}>
              <button
                type="button"
                onClick={() => onOpen(b.id)}
                className="flex w-full items-center gap-3 rounded-field border border-line-gold bg-glass-fill px-3 py-2.5 text-left transition-colors hover:bg-glass-hover"
              >
                <span className="min-w-0 flex-1">
                  <span className="text-[12.5px] text-ink">{b.kind === "ecom_table" ? copy.batch.kindEcom : copy.batch.kindPrompt}</span>
                  <span className="ml-2 text-[12px] text-ink-faint">{copy.batch.progressLabel(b.succeeded, b.total)}</span>
                </span>
                <span className={`inline-flex flex-none items-center rounded-pill border px-2.5 py-0.5 text-[11.5px] ${STATUS_CLASS[b.status]}`}>
                  {BATCH_STATUS_LABEL[b.status]}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
