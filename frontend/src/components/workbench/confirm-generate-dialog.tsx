"use client";

import { useEffect } from "react";
import { Loader2, TriangleAlert } from "lucide-react";

import { PricingConfirmDialog } from "@/components/billing/pricing-confirm-dialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useEstimateVideo } from "@/lib/api/hooks";
import type { CreateVideoRequest, LegacyVideoEstimate, VideoEstimateContract } from "@/lib/api/types";
import type { GenerateConfirmPricing } from "@/lib/api/use-generate-confirm";
import { copy } from "@/lib/copy";

export interface ConfirmGenerateDialogProps {
  open: boolean;
  request: CreateVideoRequest | null;
  submitting: boolean;
  /** Authoritative one-fetch state supplied by useGenerateConfirm for priced video forms. */
  pricing?: GenerateConfirmPricing | null;
  onConfirm: () => void;
  onCancel: () => void;
}

function legacyCompatibility(value: VideoEstimateContract | undefined): VideoEstimateContract | null {
  if (!value) return null;
  if ("pricing_contract" in value) return value;
  // Older isolated component tests/legacy-only callers may still provide the pre-union shape.
  const legacy = value as unknown as Partial<LegacyVideoEstimate>;
  return typeof legacy.estimated_credits === "number" && legacy.unit === "credits"
    ? {
        pricing_contract: "legacy_estimate",
        estimated_credits: legacy.estimated_credits,
        unit: "credits",
        note: legacy.note
      }
    : null;
}

/**
 * Exhaustive presentation for POST /videos/estimate. A billed quote delegates to
 * the common pricing dialog, legacy keeps the existing estimate view, and a true
 * deferred contract is labelled as unfinished pricing rather than fake free/zero.
 */
export function ConfirmGenerateDialog({
  open,
  request,
  submitting,
  pricing,
  onConfirm,
  onCancel
}: ConfirmGenerateDialogProps) {
  const fallbackEstimate = useEstimateVideo();
  const { mutate, reset } = fallbackEstimate;

  useEffect(() => {
    if (pricing) return;
    if (open && request) mutate(request);
    else reset();
  }, [open, pricing, request, mutate, reset]);

  const estimate = pricing?.estimate ?? legacyCompatibility(fallbackEstimate.data);
  const estimating = pricing ? pricing.phase === "estimating" : fallbackEstimate.isPending;
  const estimateFailed = pricing
    ? pricing.phase === "failed"
    : Boolean(fallbackEstimate.isError);

  if (pricing && estimate?.pricing_contract === "billing_quote") {
    return (
      <PricingConfirmDialog
        open={open}
        phase={pricing.billingPhase === "idle" ? "estimating" : pricing.billingPhase}
        quote={pricing.billingQuote}
        expiresInSeconds={pricing.expiresInSeconds}
        errorMessage={pricing.billingErrorMessage}
        onEstimate={() => void pricing.prepareBilling()}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />
    );
  }

  const deferred = estimate?.pricing_contract === "deferred_unpriced";
  const unsupportedBilledFallback = !pricing && estimate?.pricing_contract === "billing_quote";
  const confirmDisabled =
    submitting ||
    estimating ||
    estimateFailed ||
    estimate === null ||
    unsupportedBilledFallback;

  function renderEstimate() {
    if (estimating) {
      return (
        <span className="inline-flex items-center gap-1.5">
          <Loader2 size={14} strokeWidth={2} className="animate-spin" />
          {copy.confirm.estimating}
        </span>
      );
    }
    if (estimateFailed) {
      return (
        <span role="alert" className="inline-flex items-start gap-1.5 text-error-fg">
          <TriangleAlert size={14} strokeWidth={2} className="mt-0.5 flex-none" />
          {pricing?.errorMessage ?? "暂时无法获取价格，请稍后重试"}
        </span>
      );
    }
    if (!estimate) return "等待获取价格";
    switch (estimate.pricing_contract) {
      case "legacy_estimate":
        return (
          <>
            {copy.confirm.estimatePrefix}
            <span className="font-semibold text-gold-deep">{estimate.estimated_credits}</span>
            {copy.confirm.estimateSuffix}
            <span className="mt-1 block text-[12px] text-ink-faint">
              {estimate.note ?? copy.confirm.estimateNote}
            </span>
          </>
        );
      case "deferred_unpriced":
        return (
          <>
            <span className="font-semibold text-ink">延期处理／尚未闭环</span>
            {estimate.note && (
              <span className="mt-1 block text-[12px] text-ink-faint">{estimate.note}</span>
            )}
          </>
        );
      case "billing_quote":
        return "此操作需要重新获取服务端报价";
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
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

        {deferred ? (
          <p className="rounded-field bg-gold/10 px-3 py-2.5 text-[13px] text-ink-soft">
            该模式尚未纳入本轮定价闭环，提交后将按延期流程处理。
          </p>
        ) : (
          <p className="flex items-start gap-2 rounded-field bg-error-bg px-3 py-2.5 text-[13px] font-medium text-error-fg">
            <TriangleAlert size={16} strokeWidth={2} className="mt-0.5 flex-none" />
            <span>{copy.confirm.warning}</span>
          </p>
        )}

        <div className="mt-1 flex justify-end gap-2.5">
          <Button variant="soft" onClick={onCancel} disabled={submitting}>
            {copy.confirm.cancel}
          </Button>
          {estimateFailed && pricing && (
            <Button variant="soft" onClick={() => void pricing.retryEstimate()} disabled={submitting}>
              重新获取价格
            </Button>
          )}
          <Button variant="primary" onClick={onConfirm} disabled={confirmDisabled}>
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
