"use client";

import { useEffect } from "react";
import { Loader2, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useEstimateVideo } from "@/lib/api/hooks";
import type { CreateVideoRequest } from "@/lib/api/types";
import { copy } from "@/lib/copy";

export interface ConfirmGenerateDialogProps {
  open: boolean;
  request: CreateVideoRequest | null;
  submitting: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * "确定生成" confirmation shared by both workbench forms. On open it fetches a
 * credit estimate (POST /videos/estimate via the hook) and shows 预计消耗 X 积分
 * with a loading state and a graceful failure fallback ("暂无法预估，按实际结算"),
 * plus the irreversible-charge warning. 确定 runs onConfirm (the form's
 * createAndTrack); 取消 / Esc / overlay → onCancel. No fetch lives in the parent
 * forms — the estimate stays behind a lib/api hook.
 */
export function ConfirmGenerateDialog({
  open,
  request,
  submitting,
  onConfirm,
  onCancel
}: ConfirmGenerateDialogProps) {
  const estimate = useEstimateVideo();
  const { mutate, reset } = estimate;

  // Fetch the estimate each time the dialog opens with a request; clear on close.
  useEffect(() => {
    if (open && request) mutate(request);
    else reset();
  }, [open, request, mutate, reset]);

  // loading → 预计消耗 X 积分 → graceful fallback (estimate endpoint may 404).
  function renderEstimate() {
    if (estimate.isPending) {
      return (
        <span className="inline-flex items-center gap-1.5">
          <Loader2 size={14} strokeWidth={2} className="animate-spin" />
          {copy.confirm.estimating}
        </span>
      );
    }
    if (!estimate.data) return copy.confirm.estimateUnavailable;
    return (
      <>
        {copy.confirm.estimatePrefix}
        <span className="font-semibold text-gold-deep">{estimate.data.estimated_credits}</span>
        {copy.confirm.estimateSuffix}
        <span className="mt-1 block text-[12px] text-ink-faint">
          {estimate.data.note ?? copy.confirm.estimateNote}
        </span>
      </>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Esc / overlay click close the dialog → treat as cancel (never mid-submit).
        if (!next && !submitting) onCancel();
      }}
    >
      <DialogContent className="flex flex-col gap-4">
        <DialogTitle className="text-[18px] font-semibold tracking-[.5px] text-ink">
          {copy.confirm.title}
        </DialogTitle>

        <DialogDescription className="text-[13.5px] text-ink-soft" aria-live="polite">
          {renderEstimate()}
        </DialogDescription>

        <p className="flex items-start gap-2 rounded-field bg-error-bg px-3 py-2.5 text-[13px] font-medium text-error-fg">
          <TriangleAlert size={16} strokeWidth={2} className="mt-0.5 flex-none" />
          <span>{copy.confirm.warning}</span>
        </p>

        <div className="mt-1 flex justify-end gap-2.5">
          <Button variant="soft" onClick={onCancel} disabled={submitting}>
            {copy.confirm.cancel}
          </Button>
          <Button variant="primary" onClick={onConfirm} disabled={submitting}>
            {submitting ? (
              <span className="inline-flex items-center gap-1.5">
                <Loader2 size={16} strokeWidth={2} className="animate-spin" />
                {copy.confirm.confirming}
              </span>
            ) : (
              copy.confirm.confirm
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
