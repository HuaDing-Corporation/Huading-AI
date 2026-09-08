"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  billingFromApiError,
  getBillingOperation,
  parseBillingOperationLookup,
  parseBillingOperationLookupEnvelope,
  parseBillingQuote,
  parseBillingSummary,
  type BillingOperationLookupLike,
  type BillingOperationLookupParser,
  type IsAny,
  type StrictCustomBillingOperationLookup
} from "@/lib/api/billing";
import { ApiError, isApiError } from "@/lib/api/client";
import { quotaKey } from "@/lib/api/keys";
import type {
  BillingConfirmation,
  BillingKnownOperation,
  BillingOperationLookup,
  BillingOperationLookupMap,
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

interface UseBillingActionOptionsBase<
  TInput,
  TQuote,
  TResult,
  TLookup,
  TOperation extends string
> {
  operation: TOperation;
  input: TInput | null;
  estimate: (input: TInput) => Promise<TQuote>;
  submit: (input: TInput, confirmation: BillingConfirmation) => Promise<TResult>;
  lookup?: (operation: string, idempotencyKey: string) => Promise<unknown>;
  fingerprint?: (input: TInput) => string;
  parseQuote?: (quote: TQuote) => BillingQuote | null;
  billingFromResult?: (result: TResult) => BillingSummary | null;
  resultFromLookup: (lookup: TLookup) => TResult | null;
  createIdempotencyKey?: () => string;
  now?: () => number;
}

export type UseBillingActionOptions<
  TInput,
  TQuote,
  TResult,
  TLookupOrOperation = BillingKnownOperation
> = IsAny<TLookupOrOperation> extends true
  ? never
  : [unknown] extends [TLookupOrOperation]
    ? never
    : [TLookupOrOperation] extends [BillingKnownOperation]
      ? CanonicalBillingActionOptions<
          TInput,
          TQuote,
          TResult,
          Extract<TLookupOrOperation, BillingKnownOperation>
        >
      : [TLookupOrOperation] extends [BillingOperationLookupLike]
        ? StrictCustomBillingActionOptions<
            TInput,
            TQuote,
            TResult,
            Extract<TLookupOrOperation, BillingOperationLookupLike>
          >
        : never;

type CanonicalBillingActionOptions<
  TInput,
  TQuote,
  TResult,
  TOperation extends BillingKnownOperation = BillingKnownOperation
> = UseBillingActionOptionsBase<
  TInput,
  TQuote,
  TResult,
  BillingOperationLookupMap[TOperation],
  TOperation
> & {
  parseLookup?: never;
};

type CustomBillingActionOptions<
  TInput,
  TQuote,
  TResult,
  TLookup
> = UseBillingActionOptionsBase<
  TInput,
  TQuote,
  TResult,
  TLookup,
  InternalLookupOperation<TLookup>
> & {
  parseLookup: (value: unknown) => TLookup | null;
};

type StrictCustomBillingActionOptions<
  TInput,
  TQuote,
  TResult,
  TLookup
> = [StrictCustomBillingOperationLookup<TLookup>] extends [never]
  ? never
  : CustomBillingActionOptions<TInput, TQuote, TResult, TLookup>;

type InternalBillingActionOptions<
  TInput,
  TQuote,
  TResult,
  TLookup extends BillingOperationLookupLike
> = UseBillingActionOptionsBase<TInput, TQuote, TResult, TLookup, string> & {
  parseLookup?: (value: unknown) => TLookup | null;
};

type InternalStrictCustomGuard<TLookup> = [StrictCustomBillingOperationLookup<TLookup>] extends [
  never
]
  ? never
  : unknown;
type InternalLookupOperation<TLookup> = TLookup extends {
  operation: infer TOperation extends string;
}
  ? TOperation
  : never;

export interface UseBillingActionResult<
  TInput,
  TQuote,
  TResult,
  TLookup extends BillingOperationLookupLike = BillingOperationLookup
> {
  phase: BillingActionPhase;
  input: TInput | null;
  quote: TQuote | null;
  billing: BillingSummary | null;
  result: TResult | null;
  /** POST receipt only (e.g. task IDs); does NOT establish completion or billing. */
  acceptedResult: TResult | null;
  lookup: TLookup | null;
  error: unknown;
  errorMessage: string | null;
  idempotencyKey: string | null;
  expiresInSeconds: number | null;
  expired: boolean;
  canConfirm: boolean;
  /** Active bounded GET cycle; phase=querying also includes paused/unknown outcomes. */
  isLookingUp: boolean;
  estimate: () => Promise<void>;
  confirm: () => Promise<void>;
  continueLookup: () => Promise<void>;
  pauseLookup: () => void;
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
    error.status >= 500 ||
    error.code === "INVALID_BILLING_RESPONSE"
  );
}

interface BoundAttempt<TInput, TResult, TLookup extends BillingOperationLookupLike> {
  operation: string;
  input: TInput;
  fingerprint: string;
  fingerprintInput: (input: TInput) => string;
  submit: (input: TInput, confirmation: BillingConfirmation) => Promise<TResult>;
  lookup: (operation: string, idempotencyKey: string) => Promise<unknown>;
  parseLookup: (value: unknown) => TLookup | null;
  billingFromResult: (result: TResult) => BillingSummary | null;
  resultFromLookup: (lookup: TLookup) => TResult | null;
  confirmation: BillingConfirmation;
  run: number;
}

export function useBillingAction<
  TInput,
  TQuote,
  TResult,
  TOperation = BillingKnownOperation
>(
  options: IsAny<TOperation> extends true
    ? never
    : [TOperation] extends [BillingKnownOperation]
      ? CanonicalBillingActionOptions<
          TInput,
          TQuote,
          TResult,
          Extract<TOperation, BillingKnownOperation>
        >
      : never
): UseBillingActionResult<
  TInput,
  TQuote,
  TResult,
  BillingOperationLookupMap[Extract<TOperation, BillingKnownOperation>]
>;
export function useBillingAction<
  TInput,
  TQuote,
  TResult,
  TLookup
>(
  options: StrictCustomBillingActionOptions<TInput, TQuote, TResult, TLookup>
): UseBillingActionResult<
  TInput,
  TQuote,
  TResult,
  Extract<TLookup, BillingOperationLookupLike>
>;
export function useBillingAction(options: unknown): unknown {
  return useBillingActionInternal(
    options as InternalBillingActionOptions<
      unknown,
      unknown,
      unknown,
      BillingOperationLookupLike
    >
  );
}

function useBillingActionInternal<
  TInput,
  TQuote,
  TResult,
  TLookup extends BillingOperationLookupLike = BillingOperationLookup
>(
  options: InternalBillingActionOptions<TInput, TQuote, TResult, TLookup>
): UseBillingActionResult<TInput, TQuote, TResult, TLookup> {
  const queryClient = useQueryClient();
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
  const queryOwner = useRef<symbol | null>(null);
  const attemptClosed = useRef(false);
  const runId = useRef(0);
  const boundFingerprint = useRef<string | null>(null);
  const quoteRef = useRef<TQuote | null>(null);
  const parsedQuoteRef = useRef<BillingQuote | null>(null);
  const keyRef = useRef<string | null>(null);
  const attemptRef = useRef<BoundAttempt<TInput, TResult, TLookup> | null>(null);
  const pollTimers = useRef(new Map<number, () => void>());

  const [phase, setPhase] = useState<BillingActionPhase>("idle");
  const [isLookingUp, setIsLookingUp] = useState(false);
  const [quoteValue, setQuoteValue] = useState<TQuote | null>(null);
  const [billing, setBilling] = useState<BillingSummary | null>(null);
  const [result, setResult] = useState<TResult | null>(null);
  const [acceptedResult, setAcceptedResult] = useState<TResult | null>(null);
  const [lookupValue, setLookupValue] = useState<TLookup | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null);
  const [expiresInSeconds, setExpiresInSeconds] = useState<number | null>(null);

  // Refetch the shared server quota on verified ledger changes; never derive
  // a wallet by adding/subtracting this operation's credits. Depend on scalar
  // fields so repeated identical in_progress responses do not refetch forever.
  const operationId = billing?.operation_id;
  const billingStatus = billing?.status;
  const held = billing?.held_credits;
  const settled = billing?.settled_credits;
  const released = billing?.released_credits;
  useEffect(() => {
    if (operationId) void queryClient.invalidateQueries({ queryKey: quotaKey });
  }, [queryClient, operationId, billingStatus, held, settled, released]);

  useEffect(() => {
    optionsRef.current = options;
    currentFingerprint.current = fingerprint;
  }, [fingerprint, options]);

  const clearPollTimers = useCallback(() => {
    // Wake cancelled waits too: clearing a timeout alone leaves confirm() pending forever.
    for (const cancel of [...pollTimers.current.values()]) cancel();
    pollTimers.current.clear();
  }, []);

  const clearAttempt = useCallback((nextPhase: BillingActionPhase = "idle") => {
    runId.current += 1;
    clearPollTimers();
    submitOwner.current = null;
    queryOwner.current = null;
    setIsLookingUp(false);
    attemptClosed.current = false;
    boundFingerprint.current = null;
    quoteRef.current = null;
    parsedQuoteRef.current = null;
    keyRef.current = null;
    attemptRef.current = null;
    setQuoteValue(null);
    setBilling(null);
    setResult(null);
    setAcceptedResult(null);
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
      // Editing inputs cannot discard an already sent, unresolved operation.
      if (attemptRef.current !== null && !attemptClosed.current) return;
      clearAttempt();
    }
  }, [clearAttempt, fingerprint, options.operation, phase]);

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
      if (remaining === 0 && phase === "ready") setPhase("expired");
    };
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [phase, quoteValue]);

  const estimate = useCallback(async () => {
    if (attemptRef.current !== null && !attemptClosed.current) return;
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
    setAcceptedResult(null);
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
    raw: unknown,
    attempt: BoundAttempt<TInput, TResult, TLookup>
  ): boolean => {
    const envelope = parseBillingOperationLookupEnvelope(raw);
    const matchingEnvelope =
      envelope?.operation === attempt.operation &&
      envelope.idempotency_key === attempt.confirmation.idempotency_key
        ? envelope
        : null;
    if (matchingEnvelope) setBilling(matchingEnvelope.billing);
    const parsed = attempt.parseLookup(raw);
    if (
      !parsed ||
      parsed.operation !== attempt.operation ||
      parsed.idempotency_key !== attempt.confirmation.idempotency_key
    ) {
      setLookupValue(null);
      setResult(null);
      setError(
        new Error(
          matchingEnvelope
            ? "计费结果协议异常，请继续查询"
            : "计费结果确认中"
        )
      );
      setPhase("querying");
      return true;
    }
    setLookupValue(parsed);
    setBilling(parsed.billing);
    if (parsed.state === "in_progress") {
      setPhase("querying");
      return false;
    }
    if (parsed.completion_kind === "succeeded") {
      const recovered = attempt.resultFromLookup(parsed);
      if (recovered === null) {
        setResult(null);
        setError(new Error("计费结果无法确认"));
        setPhase("failed");
        attemptClosed.current = true;
        return true;
      }
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

  const pauseLookup = useCallback(() => {
    queryOwner.current = null;
    clearPollTimers();
    setIsLookingUp(false);
  }, [clearPollTimers]);

  // Bound both intervals and a hung transport. Late responses have no state owner.
  // Cancellation stops local recovery, never the server operation and never sends POST.
  const boundedWait = useCallback(<T,>(pending: Promise<T>, ms: number) =>
    new Promise<T>((resolve, reject) => {
      const finish = () => {
        window.clearTimeout(timer);
        pollTimers.current.delete(timer);
      };
      const cancel = () => { finish(); reject(new Error("计费结果确认中")); };
      const timer = window.setTimeout(cancel, ms);
      pollTimers.current.set(timer, cancel);
      pending.then((value) => { finish(); resolve(value); }, (caught) => { finish(); reject(caught); });
    }), []);

  const recoverUnknown = useCallback(async (
    attempt: BoundAttempt<TInput, TResult, TLookup>
  ) => {
    if (
      !mounted.current ||
      attempt.run !== runId.current ||
      queryOwner.current !== null
    ) return;
    const owner = Symbol("lookup-cycle");
    queryOwner.current = owner;
    const current = () => mounted.current && attempt.run === runId.current && queryOwner.current === owner;
    setIsLookingUp(true);
    setPhase("querying");
    try {
      for (let index = 0; index < 3; index += 1) {
        if (index > 0) {
          await boundedWait(new Promise<never>(() => {}), 1_000).catch(() => undefined);
        }
        if (!current()) return;
        try {
          const raw = await boundedWait(attempt.lookup(
            attempt.operation,
            attempt.confirmation.idempotency_key
          ), 10_000);
          if (!current()) return;
          if (applyLookup(raw, attempt)) return;
        } catch (caught) {
          if (!current()) return;
          if (!isApiError(caught) || caught.status !== 404) {
            const protocolBilling =
              isApiError(caught) && caught.code === "INVALID_BILLING_RESPONSE"
                ? billingFromApiError(caught)
                : null;
            if (
              protocolBilling?.idempotency_key === attempt.confirmation.idempotency_key
            ) {
              setBilling(protocolBilling);
              setResult(null);
              setError(new Error("计费结果协议异常，请继续查询"));
            } else {
              setError(new Error("计费结果确认中"));
            }
            setPhase("querying");
            // Protocol errors stop here without weakening the strict parser.
            if (isApiError(caught) && caught.code === "INVALID_BILLING_RESPONSE") return;
          }
        }
      }

      if (!current()) return;
      // Even repeated 404s are not proof of non-execution. Only a user-initiated
      // GET continuation is allowed; never replay POST or generate another key.
      setError(new Error("计费结果确认中"));
      setPhase("querying");
    } finally {
      if (current()) {
        queryOwner.current = null;
        setIsLookingUp(false);
      }
    }
  }, [applyLookup, boundedWait]);

  const submitBound = useCallback(async (
    attempt: BoundAttempt<TInput, TResult, TLookup>
  ) => {
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
      setAcceptedResult(completed);
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
        setBilling(knownBilling);
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

  const queryOriginal = useCallback(async () => {
    const attempt = attemptRef.current;
    if (!attempt || attemptClosed.current) return;
    await recoverUnknown(attempt);
  }, [recoverUnknown]);

  const confirm = useCallback(async () => {
    if (
      submitOwner.current !== null ||
      attemptClosed.current ||
      attemptRef.current !== null
    ) return;
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
    const explicitParseLookup = optionsRef.current.parseLookup;
    const parseLookup: ((value: unknown) => TLookup | null) =
      explicitParseLookup ??
      ((value) => parseBillingOperationLookup(value) as TLookup | null);
    const defaultLookup = explicitParseLookup
      ? (operation: string, idempotencyKey: string) =>
          getBillingOperation<TLookup>(
            operation as InternalLookupOperation<TLookup> & InternalStrictCustomGuard<TLookup>,
            idempotencyKey,
            explicitParseLookup as BillingOperationLookupParser<TLookup> &
              InternalStrictCustomGuard<TLookup>
          )
      : (operation: string, idempotencyKey: string) =>
          getBillingOperation(operation as BillingKnownOperation, idempotencyKey);
    const attempt: BoundAttempt<TInput, TResult, TLookup> = {
      operation: optionsRef.current.operation,
      input,
      fingerprint: attemptFingerprint,
      fingerprintInput: optionsRef.current.fingerprint ?? normalizedBillingInputFingerprint,
      submit: optionsRef.current.submit,
      lookup: optionsRef.current.lookup ?? defaultLookup,
      parseLookup,
      billingFromResult: optionsRef.current.billingFromResult ?? defaultBillingFromResult,
      resultFromLookup: optionsRef.current.resultFromLookup,
      confirmation,
      run: id
    };
    attemptRef.current = attempt;
    try {
      await submitBound(attempt);
    } finally {
      if (submitOwner.current === id) submitOwner.current = null;
    }
  }, [clearAttempt, submitBound]);

  const clearUserAttempt = useCallback(() => {
    if (attemptRef.current !== null && !attemptClosed.current) return;
    clearAttempt();
  }, [clearAttempt]);

  const expired = phase === "expired" || expiresInSeconds === 0;
  return {
    phase,
    input: options.input,
    quote: quoteValue,
    billing,
    result,
    acceptedResult,
    lookup: lookupValue,
    error,
    errorMessage: error ? messageFor(error) : null,
    idempotencyKey,
    expiresInSeconds,
    expired,
    canConfirm: phase === "ready" && !expired && quoteValue !== null,
    isLookingUp,
    estimate,
    confirm,
    continueLookup: queryOriginal,
    pauseLookup,
    retry: clearUserAttempt,
    reset: clearUserAttempt
  };
}
