"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, TriangleAlert } from "lucide-react";

import { PricingConfirmDialog } from "@/components/billing/pricing-confirm-dialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useEstimateVideo } from "@/lib/api/hooks";
import type { CreateVideoRequest, LegacyVideoEstimate, VideoEstimateContract } from "@/lib/api/types";
import type { GenerateConfirm, GenerateConfirmPricing } from "@/lib/api/use-generate-confirm";
import { copy } from "@/lib/copy";
import { isHeygenAvatarRequest, policyForEstimate } from "@/lib/api/avatar-duration-policy";
import { AvatarDurationPolicyNotice } from "./avatar-duration-policy-notice";

export interface ConfirmGenerateDialogProps {
  open: boolean;
  request: CreateVideoRequest | null;
  submitting: boolean;
  /** Authoritative one-fetch state supplied by useGenerateConfirm for priced video forms. */
  pricing?: GenerateConfirmPricing | null;
  onConfirm: (...args: Parameters<GenerateConfirm["confirm"]>) => void | Promise<void>;
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

  const fallbackValue = useMemo(() => legacyCompatibility(fallbackEstimate.data), [fallbackEstimate.data]);
  const estimate = pricing?.estimate ?? fallbackValue;
  const estimating = pricing ? pricing.phase === "estimating" : fallbackEstimate.isPending;
  const estimateFailed = pricing
    ? pricing.phase === "failed"
    : Boolean(fallbackEstimate.isError);

  // Photo and Precision are both avatar requests; Seedance/ecom must not inherit this policy.
  const needsDurationConsent = isHeygenAvatarRequest(request);
  const policy = policyForEstimate(estimate);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!open || !policy) return;
    setNow(Date.now());
    const remaining = Date.parse(policy.expires_at) - Date.now();
    if (remaining <= 0) return;
    const timer = window.setTimeout(() => setNow(Date.now()), Math.min(remaining, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [open, policy]);
  const policyReady = !!policy && Date.parse(policy.expires_at) > now;
  const requestKey = JSON.stringify(request);
  const [accepted, setAccepted] = useState(false);
  const confirming = useRef(false);
  const [localSubmitting, setLocalSubmitting] = useState(false);
  useEffect(() => {
    setAccepted(false);
  }, [open, requestKey, estimate, pricing?.billingQuote]);
  const consentMissing = needsDurationConsent && (!accepted || !policyReady);
  const busy = submitting || localSubmitting;
  const durationNotice = needsDurationConsent ? (
    <div className="min-w-0 space-y-2">
      <AvatarDurationPolicyNotice accepted={accepted} disabled={busy || !policyReady || estimating} onChange={setAccepted} />
      {!policyReady && <p role="status" className="text-[13px] text-ink-soft">{copy.confirm.avatarPolicyUnavailable}</p>}
    </div>
  ) : undefined;
  const confirmOnce = async () => {
    if (!open || busy || consentMissing || confirming.current) return;
    // Event-time validation also covers a throttled/queued expiry timer.
    if (needsDurationConsent && (!policy || Date.parse(policy.expires_at) <= Date.now())) return;
    if (!needsDurationConsent) {
      await onConfirm();
      return;
    }
    confirming.current = true;
    setLocalSubmitting(true);
    try {
      if (!pricing && request && estimate?.pricing_contract === "legacy_estimate") {
        await onConfirm(true, { request, estimate });
      } else {
        await onConfirm(true);
      }
    } finally {
      confirming.current = false;
      setLocalSubmitting(false);
    }
  };

  if (pricing && estimate?.pricing_contract === "billing_quote") {
    return (
      <PricingConfirmDialog
        open={open}
        phase={pricing.billingPhase === "idle" ? "estimating" : pricing.billingPhase}
        quote={pricing.billingQuote}
        expiresInSeconds={pricing.expiresInSeconds}
        errorMessage={pricing.billingErrorMessage}
        billing={pricing.billing}
        billingQuerying={pricing.billingPhase === "querying"}
        onContinueLookup={() => void pricing.continueBillingLookup()}
        onEstimate={() => void pricing.retryEstimate()}
        confirmationContent={durationNotice}
        confirmationDisabled={consentMissing || busy}
        onConfirm={() => void confirmOnce()}
        onCancel={onCancel}
      />
    );
  }

  const deferred = estimate?.pricing_contract === "deferred_unpriced";
  const unsupportedBilledFallback = !pricing && estimate?.pricing_contract === "billing_quote";
  const confirmDisabled =
    busy ||
    consentMissing ||
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
        if (!next && !busy) onCancel();
      }}
    >
      <DialogContent className="flex max-h-[90vh] flex-col gap-4 overflow-y-auto">
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

        {durationNotice}

        <div className="mt-1 flex flex-wrap justify-end gap-2.5">
          <Button variant="soft" onClick={onCancel} disabled={busy}>
            {copy.confirm.cancel}
          </Button>
          {estimateFailed && pricing && (
            <Button variant="soft" onClick={() => void pricing.retryEstimate()} disabled={submitting}>
              重新获取价格
            </Button>
          )}
          <Button variant="primary" onClick={() => void confirmOnce()} disabled={confirmDisabled}>
            {busy ? (
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
