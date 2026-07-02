"use client";

import { useEffect } from "react";
import { Loader2, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useEstimateBatch } from "@/lib/api/hooks";
import type { BatchRequest } from "@/lib/api/types";
import { copy } from "@/lib/copy";

export interface BatchEstimateDialogProps {
  open: boolean;
  request: BatchRequest | null;
  submitting: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * 批量生产「确认」对话框（BATCH-PROD-UI-0001）——打开时调 POST /batches/estimate，展示总条数/单条积分/
 * 总积分/当前余额；余额不足则禁用确认并提示；1080p 附「高峰可能排队较久」。零裸 fetch（估算走 hook）。
 */
export function BatchEstimateDialog({ open, request, submitting, onConfirm, onCancel }: BatchEstimateDialogProps) {
  const estimate = useEstimateBatch();
  const { mutate, reset } = estimate;

  useEffect(() => {
    if (open && request) mutate(request);
    else reset();
  }, [open, request, mutate, reset]);

  const data = estimate.data;
  const insufficient = data?.insufficient ?? false;
  const is1080 = request?.common.resolution === "1080p";
  const confirmDisabled = submitting || estimate.isPending || !data || insufficient;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !submitting) onCancel();
      }}
    >
      <DialogContent className="flex flex-col gap-4">
        <DialogTitle className="text-[18px] font-semibold tracking-[.5px] text-ink">{copy.batch.estimateTitle}</DialogTitle>

        <DialogDescription asChild>
          <div className="text-[13.5px] text-ink-soft" aria-live="polite">
            {estimate.isPending ? (
              <span className="inline-flex items-center gap-1.5">
                <Loader2 size={14} strokeWidth={2} className="animate-spin" /> {copy.batch.estimating}
              </span>
            ) : data ? (
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5">
                <dt className="text-ink-faint">{copy.batch.estimateRows(data.total_rows)}</dt>
                <dd className="text-right text-ink">{copy.batch.estimatePerRow}：{data.per_row_credits}</dd>
                <dt className="text-ink-faint">{copy.batch.estimateTotal}</dt>
                <dd className="text-right font-semibold text-gold-deep">{data.total_credits}</dd>
                <dt className="text-ink-faint">{copy.batch.estimateBalance}</dt>
                <dd className={`text-right ${insufficient ? "text-error-fg" : "text-ink"}`}>{data.balance_credits}</dd>
              </dl>
            ) : (
              copy.confirm.estimateUnavailable
            )}
          </div>
        </DialogDescription>

        {insufficient && (
          <p role="alert" className="flex items-start gap-2 rounded-field bg-error-bg px-3 py-2.5 text-[13px] font-medium text-error-fg">
            <TriangleAlert size={16} strokeWidth={2} className="mt-0.5 flex-none" />
            <span>{copy.batch.estimateInsufficient}</span>
          </p>
        )}
        {is1080 && !insufficient && (
          <p className="text-[12px] text-ink-faint">{copy.batch.estimateQueueHint}</p>
        )}

        <div className="mt-1 flex justify-end gap-2.5">
          <Button variant="soft" onClick={onCancel} disabled={submitting}>
            {copy.batch.estimateCancel}
          </Button>
          <Button variant="primary" onClick={onConfirm} disabled={confirmDisabled}>
            {submitting ? (
              <span className="inline-flex items-center gap-1.5">
                <Loader2 size={16} strokeWidth={2} className="animate-spin" /> {copy.batch.submitting}
              </span>
            ) : (
              copy.batch.estimateConfirm
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
