"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { isHeygenAvatarRequest, policyForEstimate } from "./avatar-duration-policy";
import { parseBillingQuote } from "@/lib/api/billing";
import { estimateVideo } from "@/lib/api/videos";
import type {
  BillingConfirmation,
  BillingOperationLookupFor,
  BillingQuote,
  BillingSummary,
  CreateVideoRequest,
  LegacyVideoEstimate,
  VideoAcceptedContract,
  VideoEstimateContract,
  VideoPricingContext
} from "@/lib/api/types";
import { useBillingAction, type BillingActionPhase } from "@/lib/billing/use-billing-action";

type VideoEstimatePhase = "idle" | "estimating" | "ready" | "failed";

/** The fallback dialog owns this estimate; bind it to the exact open request snapshot. */
export interface FallbackAvatarConsent {
  request: CreateVideoRequest;
  estimate: LegacyVideoEstimate;
}

export interface GenerateConfirmPricing {
  estimate: VideoEstimateContract | null;
  phase: VideoEstimatePhase;
  error: unknown;
  errorMessage: string | null;
  billingPhase: BillingActionPhase;
  billingQuote: BillingQuote | null;
  billing: BillingSummary | null;
  billingErrorMessage: string | null;
  expiresInSeconds: number | null;
  prepareBilling: () => Promise<void>;
  continueBillingLookup: () => Promise<void>;
  retryEstimate: () => Promise<void>;
}

export interface GenerateConfirm {
  open: boolean;
  request: CreateVideoRequest | null;
  submitting: boolean;
  /** Last authoritative billing result, retained after the dialog closes. */
  billing: BillingSummary | null;
  /** Present for video forms that opt in to the authoritative three-branch contract. */
  pricing: GenerateConfirmPricing | null;
  /** Open the confirm dialog with the exact validated request snapshot. */
  requestConfirm: (req: CreateVideoRequest, pricingContext?: VideoPricingContext | null) => void;
  /** Confirm the already-fetched contract; the estimate endpoint is never called here. */
  confirm: (durationPolicyAccepted?: boolean, fallbackConsent?: FallbackAvatarConsent) => Promise<void>;
  /** Close without submitting (no-op while a submit/recovery is in flight). */
  cancel: () => void;
  /** Explicitly dismiss the retained billing result. */
  dismissBilling: () => void;
}

export interface GenerateConfirmOptions {
  authoritativePricing?: boolean;
  onEstimateError?: (error: unknown) => void;
}

type GenerateSubmit = (
  request: CreateVideoRequest,
  estimate?: VideoEstimateContract,
  confirmation?: BillingConfirmation,
  pricingContext?: VideoPricingContext | null
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
  const pricingContextRef = useRef<VideoPricingContext | null>(null);
  const billingPrepareStarted = useRef(false);
  const acceptedPolicyToken = useRef<string | null>(null);
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

  function consentedRequest(input: CreateVideoRequest): CreateVideoRequest {
    if (!isHeygenAvatarRequest(input)) return input;
    const policy = policyForEstimate(cachedEstimate.current);
    if (!policy || policy.token !== acceptedPolicyToken.current || Date.parse(policy.expires_at) <= Date.now()) {
      throw new ApiError("请重新确认时长限制及费用规则。", "AVATAR_DURATION_POLICY_REQUIRED", 422);
    }
    return { ...input, avatar_duration_policy: policy.version, avatar_duration_policy_token: policy.token };
  }

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
    parseQuote: (quote) => parseBillingQuote(quote, pricingContextRef.current),
    submit: async (input, confirmation) => {
      const value = cachedEstimate.current;
      if (!value || value.pricing_contract !== "billing_quote") {
        throw new ApiError("报价数据无效，请重新获取价格。", "INVALID_BILLING_QUOTE", 502);
      }
      const context = pricingContextRef.current;
      if (!context) {
        throw new ApiError("缺少所选音色的报价校验信息。", "INVALID_VIDEO_PRICING_CONTEXT", 502);
      }
      const accepted = await submitRef.current(consentedRequest(input), value, confirmation, context);
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

  const fetchEstimate = useCallback(async (
    snapshot: CreateVideoRequest,
    pricingContext: VideoPricingContext | null
  ) => {
    const run = ++estimateRun.current;
    cachedEstimate.current = null;
    acceptedPolicyToken.current = null;
    pricingContextRef.current = pricingContext;
    billingPrepareStarted.current = false;
    setEstimate(null);
    setEstimateError(null);
    setEstimatePhase("estimating");
    resetBilling();
    try {
      const value = await estimateVideo(snapshot, pricingContext);
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

  const requestConfirm = useCallback((
    snapshot: CreateVideoRequest,
    pricingContext: VideoPricingContext | null = null
  ) => {
    setRequest(snapshot);
    setOpen(true);
    setLegacySubmitting(false);
    if (enabled) void fetchEstimate(snapshot, pricingContext);
  }, [enabled, fetchEstimate]);

  const close = useCallback(() => {
    estimateRun.current += 1;
    cachedEstimate.current = null;
    acceptedPolicyToken.current = null;
    pricingContextRef.current = null;
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
    if (enabled && open && billing.phase === "succeeded") setOpen(false);
  }, [billing.phase, enabled, open]);

  const submitting =
    legacySubmitting ||
    billing.phase === "submitting" ||
    billing.phase === "querying";

  const cancel = useCallback(() => {
    if (!submitting) close();
  }, [close, submitting]);

  const confirm = useCallback(async (durationPolicyAccepted = false, fallbackConsent?: FallbackAvatarConsent) => {
    if (!request || submitting) return;
    if (isHeygenAvatarRequest(request)) {
      // With authoritative pricing disabled, the dialog (not this hook) fetched
      // the legacy estimate. A boolean alone is never evidence of its policy.
      const consentEstimate = enabled ? cachedEstimate.current :
        fallbackConsent?.request === request && fallbackConsent.estimate.pricing_contract === "legacy_estimate"
          ? fallbackConsent.estimate : null;
      const policy = policyForEstimate(consentEstimate);
      if (!durationPolicyAccepted || !policy || Date.parse(policy.expires_at) <= Date.now()) return;
      if (!enabled) cachedEstimate.current = consentEstimate;
      acceptedPolicyToken.current = policy.token;
    }
    if (!enabled) {
      setLegacySubmitting(true);
      try {
        await submitRef.current(consentedRequest(request));
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
      await submitRef.current(consentedRequest(request), contract, undefined, pricingContextRef.current);
    } catch {
      // The form owns user-facing legacy/deferred errors.
    } finally {
      close();
    }
  }, [billing, close, enabled, estimatePhase, request, submitting]);

  const retryEstimate = useCallback(async () => {
    if (!request || submitting) return;
    await fetchEstimate(request, pricingContextRef.current);
  }, [fetchEstimate, request, submitting]);

  const dismissBilling = useCallback(() => {
    close();
  }, [close]);

  return {
    open,
    request,
    submitting,
    billing: billing.billing,
    pricing: enabled
      ? {
          estimate,
          phase: estimatePhase,
          error: estimateError,
          errorMessage: estimateError ? errorMessage(estimateError) : null,
          billingPhase: billing.phase,
          billingQuote: billing.quote,
          billing: billing.billing,
          billingErrorMessage: billing.errorMessage,
          expiresInSeconds: billing.expiresInSeconds,
          prepareBilling,
          continueBillingLookup: billing.continueLookup,
          retryEstimate
        }
      : null,
    requestConfirm,
    confirm,
    cancel,
    dismissBilling
  };
}
