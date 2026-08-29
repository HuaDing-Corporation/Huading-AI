"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  billingFromApiError,
  getBillingOperation,
  parseBillingOperationLookup,
  parseBillingQuote,
  parseBillingSummary
} from "@/lib/api/billing";
import { ApiError, isApiError } from "@/lib/api/client";
import type {
  BillingConfirmation,
  BillingOperationLookup,
  BillingQuote,
  BillingSummary
} from "@/lib/api/types";

export type BillingActionPhase =
  | "idle"
  | "estimating"
  | "ready"
  | "submitting"
  | "querying"
  | "succeeded"
  | "failed"
  | "expired";

export interface UseBillingActionOptions<TInput, TQuote, TResult> {
  operation: string;
  input: TInput | null;
  estimate: (input: TInput) => Promise<TQuote>;
  submit: (input: TInput, confirmation: BillingConfirmation) => Promise<TResult>;
  lookup?: (operation: string, idempotencyKey: string) => Promise<BillingOperationLookup>;
  fingerprint?: (input: TInput) => string;
  parseQuote?: (quote: TQuote) => BillingQuote | null;
  billingFromResult?: (result: TResult) => BillingSummary | null;
  resultFromLookup?: (lookup: BillingOperationLookup) => TResult | null;
  createIdempotencyKey?: () => string;
  now?: () => number;
}

export interface UseBillingActionResult<TInput, TQuote, TResult> {
  phase: BillingActionPhase;
  input: TInput | null;
  quote: TQuote | null;
  billing: BillingSummary | null;
  result: TResult | null;
  lookup: BillingOperationLookup | null;
  error: unknown;
  errorMessage: string | null;
  idempotencyKey: string | null;
  expiresInSeconds: number | null;
  expired: boolean;
  canConfirm: boolean;
  estimate: () => Promise<void>;
  confirm: () => Promise<void>;
  continueLookup: () => Promise<void>;
  retry: () => void;
  reset: () => void;
}

function canonical(value: unknown, seen: WeakSet<object>): string {
  if (value === null || typeof value !== "object") {
    const encoded = JSON.stringify(value);
    return encoded === undefined ? String(value) : encoded;
  }
  if (seen.has(value)) throw new TypeError("Billing input must not contain cycles");
  seen.add(value);
  try {
    if (Array.isArray(value)) return `[${value.map((entry) => canonical(entry, seen)).join(",")}]`;
    if (value instanceof Date) return JSON.stringify(value.toISOString());
    const object = value as Record<string, unknown>;
    return `{${Object.keys(object)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonical(object[key], seen)}`)
      .join(",")}}`;
  } finally {
    seen.delete(value);
  }
}

export function normalizedBillingInputFingerprint(input: unknown): string {
  return canonical(input, new WeakSet());
}

function defaultUuid(): string {
  return globalThis.crypto.randomUUID();
}

function defaultBillingFromResult<TResult>(result: TResult): BillingSummary | null {
  if (typeof result !== "object" || result === null || !("billing" in result)) return null;
  return parseBillingSummary((result as { billing: unknown }).billing);
}

function messageFor(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "操作失败，请重试";
}

function isUnknownPostResult(error: unknown): boolean {
  return (
    !isApiError(error) ||
    error.status === 0 ||
    error.status === 502 ||
    error.status === 503 ||
    error.status === 504 ||
    error.code === "INVALID_BILLING_RESPONSE"
  );
}

interface BoundAttempt<TInput, TResult> {
  operation: string;
  input: TInput;
  fingerprint: string;
  fingerprintInput: (input: TInput) => string;
  submit: (input: TInput, confirmation: BillingConfirmation) => Promise<TResult>;
  lookup: (operation: string, idempotencyKey: string) => Promise<BillingOperationLookup>;
  billingFromResult: (result: TResult) => BillingSummary | null;
  resultFromLookup?: (lookup: BillingOperationLookup) => TResult | null;
  now: () => number;
  confirmation: BillingConfirmation;
  replayed: boolean;
  run: number;
}

export function useBillingAction<TInput, TQuote, TResult>(
  options: UseBillingActionOptions<TInput, TQuote, TResult>
): UseBillingActionResult<TInput, TQuote, TResult> {
  const optionsRef = useRef(options);
  // Deliberately recompute on every render: React state should be immutable, but a
  // billing boundary must also detect an in-place mutation of the same object.
  const fingerprint =
    options.input === null
      ? null
      : (options.fingerprint ?? normalizedBillingInputFingerprint)(options.input);
  const previousBinding = useRef({ operation: options.operation, fingerprint });
  const currentFingerprint = useRef(fingerprint);
  const mounted = useRef(true);
  const submitOwner = useRef<number | null>(null);
  const queryOwner = useRef<number | null>(null);
  const attemptClosed = useRef(false);
  const runId = useRef(0);
  const boundFingerprint = useRef<string | null>(null);
  const quoteRef = useRef<TQuote | null>(null);
  const parsedQuoteRef = useRef<BillingQuote | null>(null);
  const keyRef = useRef<string | null>(null);
  const attemptRef = useRef<BoundAttempt<TInput, TResult> | null>(null);
  const pollTimers = useRef(new Set<number>());
  const submitBoundRef = useRef<(attempt: BoundAttempt<TInput, TResult>) => Promise<void>>(
    async () => undefined
  );

  const [phase, setPhase] = useState<BillingActionPhase>("idle");
  const [quoteValue, setQuoteValue] = useState<TQuote | null>(null);
  const [billing, setBilling] = useState<BillingSummary | null>(null);
  const [result, setResult] = useState<TResult | null>(null);
  const [lookupValue, setLookupValue] = useState<BillingOperationLookup | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null);
  const [expiresInSeconds, setExpiresInSeconds] = useState<number | null>(null);

  useEffect(() => {
    optionsRef.current = options;
    currentFingerprint.current = fingerprint;
  }, [fingerprint, options]);

  const clearPollTimers = useCallback(() => {
    for (const timer of pollTimers.current) window.clearTimeout(timer);
    pollTimers.current.clear();
  }, []);

  const clearAttempt = useCallback((nextPhase: BillingActionPhase = "idle") => {
    runId.current += 1;
    clearPollTimers();
    submitOwner.current = null;
    queryOwner.current = null;
    attemptClosed.current = false;
    boundFingerprint.current = null;
    quoteRef.current = null;
    parsedQuoteRef.current = null;
    keyRef.current = null;
    attemptRef.current = null;
    setQuoteValue(null);
    setBilling(null);
    setResult(null);
    setLookupValue(null);
    setError(null);
    setIdempotencyKey(null);
    setExpiresInSeconds(null);
    setPhase(nextPhase);
  }, [clearPollTimers]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      runId.current += 1;
      clearPollTimers();
    };
  }, [clearPollTimers]);

  useEffect(() => {
    if (
      previousBinding.current.operation !== options.operation ||
      previousBinding.current.fingerprint !== fingerprint
    ) {
      previousBinding.current = { operation: options.operation, fingerprint };
      clearAttempt();
    }
  }, [clearAttempt, fingerprint, options.operation]);

  useEffect(() => {
    const parsed = parsedQuoteRef.current;
    if (!parsed) return;
    const update = () => {
      const remaining = Math.max(
        0,
        Math.ceil((Date.parse(parsed.expires_at) - (optionsRef.current.now?.() ?? Date.now())) / 1000)
      );
      if (!mounted.current) return;
      setExpiresInSeconds(remaining);
      if (remaining === 0 && (phase === "ready" || phase === "submitting")) setPhase("expired");
    };
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [phase, quoteValue]);

  const estimate = useCallback(async () => {
    const estimateOptions = optionsRef.current;
    const input = estimateOptions.input;
    const operation = estimateOptions.operation;
    const startFingerprint = currentFingerprint.current;
    if (input === null || startFingerprint === null) return;
    const id = ++runId.current;
    submitOwner.current = null;
    queryOwner.current = null;
    attemptClosed.current = false;
    attemptRef.current = null;
    keyRef.current = null;
    setIdempotencyKey(null);
    setBilling(null);
    setResult(null);
    setLookupValue(null);
    setError(null);
    setPhase("estimating");
    try {
      const raw = await estimateOptions.estimate(input);
      if (!mounted.current || id !== runId.current || currentFingerprint.current !== startFingerprint) return;
      const parsed = estimateOptions.parseQuote
        ? estimateOptions.parseQuote(raw)
        : parseBillingQuote(raw);
      if (!parsed || parsed.operation !== operation) {
        throw new ApiError("报价数据无效，请重新获取价格。", "INVALID_BILLING_QUOTE", 502);
      }
      quoteRef.current = raw;
      parsedQuoteRef.current = parsed;
      boundFingerprint.current = startFingerprint;
      setQuoteValue(raw);
      const remaining = Math.max(
        0,
        Math.ceil((Date.parse(parsed.expires_at) - (estimateOptions.now?.() ?? Date.now())) / 1000)
      );
      setExpiresInSeconds(remaining);
      setPhase(remaining > 0 ? "ready" : "expired");
    } catch (caught) {
      if (!mounted.current || id !== runId.current) return;
      quoteRef.current = null;
      parsedQuoteRef.current = null;
      setQuoteValue(null);
      setError(caught);
      setPhase("failed");
    }
  }, []);

  const applyLookup = useCallback((
    raw: BillingOperationLookup,
    attempt: BoundAttempt<TInput, TResult>
  ): boolean => {
    const parsed = parseBillingOperationLookup(raw);
    if (!parsed) return false;
    setLookupValue(parsed);
    setBilling(parsed.billing);
    if (parsed.state === "in_progress") {
      setPhase("querying");
      return false;
    }
    if (parsed.completion_kind === "succeeded") {
      const recovered = attempt.resultFromLookup
        ? attempt.resultFromLookup(parsed)
        : (parsed.result as TResult);
      setResult(recovered);
      setError(null);
      setPhase("succeeded");
      attemptClosed.current = true;
    } else {
      setError(parsed.completion_kind === "failed" ? parsed.failure : parsed.resource);
      setPhase("failed");
      attemptClosed.current = true;
    }
    return true;
  }, []);

  const waitForNextLookup = useCallback(
    () =>
      new Promise<void>((resolve) => {
        const timer = window.setTimeout(() => {
          pollTimers.current.delete(timer);
          resolve();
        }, 1_000);
        pollTimers.current.add(timer);
      }),
    []
  );

  const recoverUnknown = useCallback(async (attempt: BoundAttempt<TInput, TResult>) => {
    if (
      !mounted.current ||
      attempt.run !== runId.current ||
      queryOwner.current !== null
    ) return;
    queryOwner.current = attempt.run;
    setPhase("querying");
    let onlyNotFound = true;
    try {
      for (let index = 0; index < 3; index += 1) {
        if (index > 0) await waitForNextLookup();
        if (!mounted.current || attempt.run !== runId.current) return;
        try {
          const raw = await attempt.lookup(
            attempt.operation,
            attempt.confirmation.idempotency_key
          );
          if (!mounted.current || attempt.run !== runId.current) return;
          const parsed = parseBillingOperationLookup(raw);
          if (
            !parsed ||
            parsed.operation !== attempt.operation ||
            parsed.idempotency_key !== attempt.confirmation.idempotency_key
          ) {
            setError(new Error("计费结果确认中"));
            setPhase("querying");
            return;
          }
          onlyNotFound = false;
          if (applyLookup(parsed, attempt)) return;
        } catch (caught) {
          if (!mounted.current || attempt.run !== runId.current) return;
          if (!isApiError(caught) || caught.status !== 404) {
            setError(new Error("计费结果确认中"));
            setPhase("querying");
            return;
          }
        }
      }

      if (!mounted.current || attempt.run !== runId.current) return;
      if (!onlyNotFound) {
        setError(new Error("计费结果确认中"));
        setPhase("querying");
        return;
      }
      const parsedQuote = parsedQuoteRef.current;
      const expired =
        !parsedQuote ||
        Date.parse(parsedQuote.expires_at) <= attempt.now();
      if (expired || currentFingerprint.current !== attempt.fingerprint) {
        clearAttempt("expired");
        return;
      }
      if (attempt.replayed) {
        setError(new Error("计费结果确认中"));
        setPhase("querying");
        return;
      }

      attempt.replayed = true;
      if (queryOwner.current === attempt.run) queryOwner.current = null;
      const acquiredSubmit = submitOwner.current === null;
      if (submitOwner.current !== null && submitOwner.current !== attempt.run) return;
      if (acquiredSubmit) submitOwner.current = attempt.run;
      try {
        await submitBoundRef.current(attempt);
      } finally {
        if (acquiredSubmit && submitOwner.current === attempt.run) submitOwner.current = null;
      }
    } finally {
      if (queryOwner.current === attempt.run) queryOwner.current = null;
    }
  }, [applyLookup, clearAttempt, waitForNextLookup]);

  const submitBound = useCallback(async (attempt: BoundAttempt<TInput, TResult>) => {
    if (!mounted.current || attempt.run !== runId.current) return;
    setError(null);
    setPhase("submitting");
    try {
      if (
        attempt.fingerprint !== currentFingerprint.current ||
        attempt.fingerprintInput(attempt.input) !== attempt.fingerprint
      ) {
        clearAttempt();
        return;
      }
      const completed = await attempt.submit(attempt.input, attempt.confirmation);
      if (!mounted.current || attempt.run !== runId.current) return;
      const parsedBilling = attempt.billingFromResult(completed);
      if (!parsedBilling || parsedBilling.idempotency_key !== attempt.confirmation.idempotency_key) {
        throw new ApiError("提交结果无法确认。", "INVALID_BILLING_RESPONSE", 502);
      }
      setBilling(parsedBilling);
      if (parsedBilling.status === "released") {
        setError(new Error("操作未完成，冻结积分已释放"));
        setPhase("failed");
      } else {
        setResult(completed);
        setError(null);
        setPhase("succeeded");
      }
      attemptClosed.current = true;
    } catch (caught) {
      if (!mounted.current || attempt.run !== runId.current) return;
      const knownBilling = billingFromApiError(caught);
      if (isApiError(caught) && caught.code === "PRICE_CHANGED") {
        clearAttempt();
        setError(caught);
        setPhase("failed");
      } else if (
        knownBilling?.status === "released" &&
        knownBilling.idempotency_key === attempt.confirmation.idempotency_key
      ) {
        setBilling(knownBilling);
        setError(caught);
        setPhase("failed");
        attemptClosed.current = true;
      } else if (
        knownBilling &&
        knownBilling.idempotency_key === attempt.confirmation.idempotency_key
      ) {
        setError(caught);
        await recoverUnknown(attempt);
      } else if (isUnknownPostResult(caught)) {
        setError(caught);
        await recoverUnknown(attempt);
      } else {
        setError(caught);
        setPhase("failed");
        attemptClosed.current = true;
      }
    }
  }, [clearAttempt, recoverUnknown]);
  useEffect(() => {
    submitBoundRef.current = submitBound;
  }, [submitBound]);

  const queryOriginal = useCallback(async () => {
    const attempt = attemptRef.current;
    if (!attempt) return;
    await recoverUnknown(attempt);
  }, [recoverUnknown]);

  const confirm = useCallback(async () => {
    if (submitOwner.current !== null || attemptClosed.current) return;
    const input = optionsRef.current.input;
    const rawQuote = quoteRef.current;
    const parsedQuote = parsedQuoteRef.current;
    const attemptFingerprint = boundFingerprint.current;
    if (
      input === null ||
      !rawQuote ||
      !parsedQuote ||
      !attemptFingerprint ||
      attemptFingerprint !== currentFingerprint.current ||
      Date.parse(parsedQuote.expires_at) <= (optionsRef.current.now?.() ?? Date.now())
    ) {
      clearAttempt("expired");
      return;
    }
    const key = keyRef.current ?? (optionsRef.current.createIdempotencyKey ?? defaultUuid)();
    keyRef.current = key;
    setIdempotencyKey(key);
    const confirmation = { idempotency_key: key, quote_token: parsedQuote.quote_token };
    const id = ++runId.current;
    submitOwner.current = id;
    const attempt: BoundAttempt<TInput, TResult> = {
      operation: optionsRef.current.operation,
      input,
      fingerprint: attemptFingerprint,
      fingerprintInput: optionsRef.current.fingerprint ?? normalizedBillingInputFingerprint,
      submit: optionsRef.current.submit,
      lookup: optionsRef.current.lookup ?? getBillingOperation,
      billingFromResult: optionsRef.current.billingFromResult ?? defaultBillingFromResult,
      resultFromLookup: optionsRef.current.resultFromLookup,
      now: optionsRef.current.now ?? Date.now,
      confirmation,
      replayed: false,
      run: id
    };
    attemptRef.current = attempt;
    try {
      await submitBound(attempt);
    } finally {
      if (submitOwner.current === id) submitOwner.current = null;
    }
  }, [clearAttempt, submitBound]);

  const retry = useCallback(() => clearAttempt(), [clearAttempt]);

  const expired = phase === "expired" || expiresInSeconds === 0;
  return {
    phase,
    input: options.input,
    quote: quoteValue,
    billing,
    result,
    lookup: lookupValue,
    error,
    errorMessage: error ? messageFor(error) : null,
    idempotencyKey,
    expiresInSeconds,
    expired,
    canConfirm: phase === "ready" && !expired && quoteValue !== null,
    estimate,
    confirm,
    continueLookup: queryOriginal,
    retry,
    reset: clearAttempt
  };
}
