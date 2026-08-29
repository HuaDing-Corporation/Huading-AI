"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { estimateVideo } from "@/lib/api/videos";
import type {
  BillingConfirmation,
  BillingOperationLookupFor,
  BillingQuote,
  CreateVideoRequest,
  VideoAcceptedContract,
  VideoEstimateContract
} from "@/lib/api/types";
import { useBillingAction, type BillingActionPhase } from "@/lib/billing/use-billing-action";

type VideoEstimatePhase = "idle" | "estimating" | "ready" | "failed";

export interface GenerateConfirmPricing {
  estimate: VideoEstimateContract | null;
  phase: VideoEstimatePhase;
  error: unknown;
  errorMessage: string | null;
  billingPhase: BillingActionPhase;
  billingQuote: BillingQuote | null;
  billingErrorMessage: string | null;
  expiresInSeconds: number | null;
  prepareBilling: () => Promise<void>;
  retryEstimate: () => Promise<void>;
}

export interface GenerateConfirm {
  open: boolean;
  request: CreateVideoRequest | null;
  submitting: boolean;
  /** Present for video forms that opt in to the authoritative three-branch contract. */
  pricing: GenerateConfirmPricing | null;
  /** Open the confirm dialog with the exact validated request snapshot. */
  requestConfirm: (req: CreateVideoRequest) => void;
  /** Confirm the already-fetched contract; the estimate endpoint is never called here. */
  confirm: () => Promise<void>;
  /** Close without submitting (no-op while a submit/recovery is in flight). */
  cancel: () => void;
}

export interface GenerateConfirmOptions {
  authoritativePricing?: boolean;
  onEstimateError?: (error: unknown) => void;
}

type GenerateSubmit = (
  request: CreateVideoRequest,
  estimate?: VideoEstimateContract,
  confirmation?: BillingConfirmation
) => Promise<VideoAcceptedContract | void>;

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "暂时无法获取价格，请稍后重试";
}

/**
 * Shared confirm lifecycle. New priced video paths opt into `authoritativePricing`:
 * one estimate request selects the billing/legacy/deferred branch, and the exact
 * returned quote is rebound into the common billing state machine without a
 * second HTTP estimate. Older legacy-only consumers keep the original lifecycle.
 */
export function useGenerateConfirm(
  submit: GenerateSubmit,
  options: GenerateConfirmOptions = {}
): GenerateConfirm {
  const enabled = options.authoritativePricing === true;
  const submitRef = useRef(submit);
  const optionsRef = useRef(options);
  const estimateRun = useRef(0);
  const cachedEstimate = useRef<VideoEstimateContract | null>(null);
  const billingPrepareStarted = useRef(false);
  const [open, setOpen] = useState(false);
  const [request, setRequest] = useState<CreateVideoRequest | null>(null);
  const [legacySubmitting, setLegacySubmitting] = useState(false);
  const [estimate, setEstimate] = useState<VideoEstimateContract | null>(null);
  const [estimatePhase, setEstimatePhase] = useState<VideoEstimatePhase>("idle");
  const [estimateError, setEstimateError] = useState<unknown>(null);

  useEffect(() => {
    submitRef.current = submit;
    optionsRef.current = options;
  }, [options, submit]);

  const billing = useBillingAction<
    CreateVideoRequest,
    BillingQuote,
    VideoAcceptedContract,
    "video_create"
  >({
    operation: "video_create",
    input: enabled && estimate?.pricing_contract === "billing_quote" ? request : null,
    estimate: async () => {
      const value = cachedEstimate.current;
      if (!value || value.pricing_contract !== "billing_quote") {
        throw new ApiError("报价数据无效，请重新获取价格。", "INVALID_BILLING_QUOTE", 502);
      }
      return value;
    },
    submit: async (input, confirmation) => {
      const value = cachedEstimate.current;
      if (!value || value.pricing_contract !== "billing_quote") {
        throw new ApiError("报价数据无效，请重新获取价格。", "INVALID_BILLING_QUOTE", 502);
      }
      const accepted = await submitRef.current(input, value, confirmation);
      if (!accepted || accepted.pricing_contract !== "billing_quote") {
        throw new ApiError(
          "视频创建响应的定价协议异常，请稍后查询任务状态。",
          "INVALID_VIDEO_ACCEPTED_CONTRACT",
          502
        );
      }
      return accepted;
    },
    resultFromLookup: (lookup: BillingOperationLookupFor<"video_create">) => {
      if (
        lookup.state !== "completed" ||
        lookup.completion_kind !== "succeeded" ||
        lookup.result_type !== "video_task"
      ) {
        return null;
      }
      return {
        id: lookup.result.task_id,
        task_id: lookup.result.task_id,
        status: lookup.result.status,
        pricing_contract: "billing_quote",
        billing: lookup.billing
      };
    }
  });
  const estimateBilling = billing.estimate;
  const resetBilling = billing.reset;

  const fetchEstimate = useCallback(async (snapshot: CreateVideoRequest) => {
    const run = ++estimateRun.current;
    cachedEstimate.current = null;
    billingPrepareStarted.current = false;
    setEstimate(null);
    setEstimateError(null);
    setEstimatePhase("estimating");
    resetBilling();
    try {
      const value = await estimateVideo(snapshot);
      if (run !== estimateRun.current) return;
      cachedEstimate.current = value;
      setEstimate(value);
      setEstimatePhase("ready");
    } catch (caught) {
      if (run !== estimateRun.current) return;
      setEstimateError(caught);
      setEstimatePhase("failed");
      optionsRef.current.onEstimateError?.(caught);
    }
  }, [resetBilling]);

  const prepareBilling = useCallback(async () => {
    if (billingPrepareStarted.current) return;
    billingPrepareStarted.current = true;
    await estimateBilling();
  }, [estimateBilling]);

  // Bind the already-fetched billing quote after useBillingAction has observed
  // its new input fingerprint. PricingConfirmDialog calls the same guarded
  // function, so render/effect ordering cannot start a duplicate preparation.
  useEffect(() => {
    if (enabled && estimate?.pricing_contract === "billing_quote" && billing.phase === "idle") {
      billingPrepareStarted.current = false;
      const timer = window.setTimeout(() => void prepareBilling(), 0);
      return () => window.clearTimeout(timer);
    }
  }, [billing.phase, enabled, estimate, prepareBilling]);

  const requestConfirm = useCallback((snapshot: CreateVideoRequest) => {
    setRequest(snapshot);
    setOpen(true);
    setLegacySubmitting(false);
    if (enabled) void fetchEstimate(snapshot);
  }, [enabled, fetchEstimate]);

  const close = useCallback(() => {
    estimateRun.current += 1;
    cachedEstimate.current = null;
    billingPrepareStarted.current = false;
    setOpen(false);
    setRequest(null);
    setEstimate(null);
    setEstimateError(null);
    setEstimatePhase("idle");
    setLegacySubmitting(false);
    resetBilling();
  }, [resetBilling]);

  useEffect(() => {
    if (enabled && open && billing.phase === "succeeded") close();
  }, [billing.phase, close, enabled, open]);

  const submitting =
    legacySubmitting ||
    billing.phase === "submitting" ||
    billing.phase === "querying";

  const cancel = useCallback(() => {
    if (!submitting) close();
  }, [close, submitting]);

  const confirm = useCallback(async () => {
    if (!request || submitting) return;
    if (!enabled) {
      setLegacySubmitting(true);
      try {
        await submitRef.current(request);
      } catch {
        // The form owns user-facing legacy errors; keep the event promise handled.
      } finally {
        close();
      }
      return;
    }

    const contract = cachedEstimate.current;
    if (!contract || estimatePhase !== "ready") return;
    if (contract.pricing_contract === "billing_quote") {
      await billing.confirm();
      return;
    }

    setLegacySubmitting(true);
    try {
      await submitRef.current(request, contract);
    } catch {
      // The form owns user-facing legacy/deferred errors.
    } finally {
      close();
    }
  }, [billing, close, enabled, estimatePhase, request, submitting]);

  const retryEstimate = useCallback(async () => {
    if (!request || submitting) return;
    await fetchEstimate(request);
  }, [fetchEstimate, request, submitting]);

  return {
    open,
    request,
    submitting,
    pricing: enabled
      ? {
          estimate,
          phase: estimatePhase,
          error: estimateError,
          errorMessage: estimateError ? errorMessage(estimateError) : null,
          billingPhase: billing.phase,
          billingQuote: billing.quote,
          billingErrorMessage: billing.errorMessage,
          expiresInSeconds: billing.expiresInSeconds,
          prepareBilling,
          retryEstimate
        }
      : null,
    requestConfirm,
    confirm,
    cancel
  };
}
