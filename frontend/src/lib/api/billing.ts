import { apiFetch, ApiError, isApiError } from "./client";
import type {
  BillingConfirmation,
  BillingOperationLookup,
  BillingQuote,
  BillingSummary
} from "./types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DECIMAL = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;
const FAILURE_CODE = /^[A-Z][A-Z0-9_]{0,63}$/;
const SUMMARY_KEYS = [
  "operation_id",
  "idempotency_key",
  "status",
  "requested_credits",
  "held_credits",
  "settled_credits",
  "released_credits"
] as const;

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === keys.length && actual.every((key) => keys.includes(key));
}

function nonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function decimalString(value: unknown): value is string {
  return typeof value === "string" && DECIMAL.test(value);
}

function isoDate(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (!match) return false;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, , offsetHour, offsetMinute] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return (
    month >= 1 &&
    month <= 12 &&
    day >= 1 &&
    day <= daysInMonth &&
    Number(hourText) <= 23 &&
    Number(minuteText) <= 59 &&
    Number(secondText) <= 59 &&
    (offsetHour === undefined ||
      (Number(offsetHour) <= 23 && Number(offsetMinute) <= 59)) &&
    Number.isFinite(Date.parse(value))
  );
}

function rateScope(value: unknown): value is "tenant_overridable" | "platform_fixed" {
  return value === "tenant_overridable" || value === "platform_fixed";
}

function rateSource(
  value: unknown
): value is "tenant_rate" | "platform_rate" | "code_default" | "fixed_policy" {
  return (
    value === "tenant_rate" ||
    value === "platform_rate" ||
    value === "code_default" ||
    value === "fixed_policy"
  );
}

function validProvenance(value: Record<string, unknown>): boolean {
  if (!rateSource(value.rate_source)) return false;
  if (value.rate_source === "tenant_rate" || value.rate_source === "platform_rate") {
    return (
      nonEmptyString(value.rate_id) &&
      isoDate(value.effective_at) &&
      value.policy_key === null &&
      value.policy_version === null
    );
  }
  return (
    value.rate_id === null &&
    value.effective_at === null &&
    nonEmptyString(value.policy_key) &&
    nonNegativeInteger(value.policy_version) &&
    value.policy_version >= 1
  );
}

export function parseBillingSummary(value: unknown): BillingSummary | null {
  if (!record(value) || !exactKeys(value, SUMMARY_KEYS)) return null;
  const status = value.status;
  if (
    status !== "reserved" &&
    status !== "settled" &&
    status !== "partially_settled" &&
    status !== "released"
  ) {
    return null;
  }
  if (
    typeof value.operation_id !== "string" ||
    value.operation_id.length === 0 ||
    typeof value.idempotency_key !== "string" ||
    !UUID.test(value.idempotency_key) ||
    !nonNegativeInteger(value.requested_credits) ||
    !nonNegativeInteger(value.held_credits) ||
    !nonNegativeInteger(value.settled_credits) ||
    !nonNegativeInteger(value.released_credits)
  ) {
    return null;
  }

  const { requested_credits, held_credits, settled_credits, released_credits } = value;
  if (requested_credits !== held_credits + settled_credits + released_credits) return null;
  const validState =
    (status === "reserved" &&
      held_credits === requested_credits &&
      settled_credits === 0 &&
      released_credits === 0) ||
    (status === "settled" &&
      held_credits === 0 &&
      settled_credits === requested_credits &&
      released_credits === 0) ||
    (status === "partially_settled" &&
      held_credits === 0 &&
      settled_credits > 0 &&
      released_credits > 0) ||
    (status === "released" &&
      held_credits === 0 &&
      settled_credits === 0 &&
      released_credits === requested_credits);
  return validState ? (value as unknown as BillingSummary) : null;
}

export function billingFromApiError(error: unknown): BillingSummary | null {
  if (!isApiError(error) || !record(error.detail)) return null;
  return parseBillingSummary(error.detail.billing);
}

export function billingHeaders(confirmation: BillingConfirmation): Record<string, string> {
  return {
    "Idempotency-Key": confirmation.idempotency_key,
    "X-Huading-Quote": confirmation.quote_token
  };
}

const QUOTE_KEYS = [
  "pricing_contract",
  "operation",
  "pricing_shape",
  "unit",
  "quantity",
  "unit_credits",
  "rate_scope",
  "rate_source",
  "subtotal_credits",
  "payable_credits",
  "breakdown",
  "disclosures",
  "quote_token",
  "expires_at"
] as const;
const LINE_KEYS = [
  "operation",
  "capability",
  "unit",
  "quantity",
  "unit_credits",
  "subtotal_credits",
  "rate_scope",
  "rate_source",
  "rate_id",
  "effective_at",
  "policy_key",
  "policy_version",
  "label"
] as const;
const DISCLOSURE_KEYS = [
  "key",
  "rendered_text",
  "copy_version",
  "unit",
  "rate_scope",
  "rate_source",
  "rate_id",
  "effective_at",
  "policy_key",
  "policy_version",
  "reference_unit_credits"
] as const;

function pricingLine(value: unknown, operation: string): boolean {
  return (
    record(value) &&
    exactKeys(value, LINE_KEYS) &&
    value.operation === operation &&
    nonEmptyString(value.capability) &&
    nonEmptyString(value.unit) &&
    decimalString(value.quantity) &&
    decimalString(value.unit_credits) &&
    decimalString(value.subtotal_credits) &&
    rateScope(value.rate_scope) &&
    validProvenance(value) &&
    nonEmptyString(value.label)
  );
}

function disclosure(value: unknown): boolean {
  return (
    record(value) &&
    exactKeys(value, DISCLOSURE_KEYS) &&
    nonEmptyString(value.key) &&
    nonEmptyString(value.rendered_text) &&
    nonNegativeInteger(value.copy_version) &&
    value.copy_version >= 1 &&
    nonEmptyString(value.unit) &&
    rateScope(value.rate_scope) &&
    validProvenance(value) &&
    decimalString(value.reference_unit_credits)
  );
}

export function parseBillingQuote(value: unknown): BillingQuote | null {
  if (
    !record(value) ||
    !exactKeys(value, QUOTE_KEYS) ||
    value.pricing_contract !== "billing_quote" ||
    !nonEmptyString(value.operation) ||
    !decimalString(value.subtotal_credits) ||
    !nonNegativeInteger(value.payable_credits) ||
    !Array.isArray(value.breakdown) ||
    !Array.isArray(value.disclosures) ||
    !value.disclosures.every(disclosure) ||
    !nonEmptyString(value.quote_token) ||
    !isoDate(value.expires_at)
  ) {
    return null;
  }

  if (value.pricing_shape === "simple") {
    if (
      !nonEmptyString(value.unit) ||
      !decimalString(value.quantity) ||
      !decimalString(value.unit_credits) ||
      !rateScope(value.rate_scope) ||
      !rateSource(value.rate_source) ||
      value.breakdown.length !== 0
    ) {
      return null;
    }
    return value as unknown as BillingQuote;
  }
  if (
    value.pricing_shape !== "composite" ||
    value.unit !== null ||
    value.quantity !== null ||
    value.unit_credits !== null ||
    value.rate_scope !== null ||
    value.rate_source !== null ||
    value.breakdown.length === 0 ||
    !value.breakdown.every((entry) => pricingLine(entry, value.operation as string))
  ) {
    return null;
  }
  return value as unknown as BillingQuote;
}

const LOOKUP_KEYS = [
  "operation",
  "idempotency_key",
  "state",
  "completion_kind",
  "billing",
  "result_type",
  "result_id",
  "resource",
  "result",
  "failure"
] as const;

function nullablePayload(value: unknown): boolean {
  return value === null || record(value);
}

function resultId(value: unknown): boolean {
  return value === null || nonEmptyString(value);
}

function failure(value: unknown): boolean {
  if (!record(value) || !exactKeys(value, ["code", "original_http_status", "detail"])) return false;
  if (
    typeof value.code !== "string" ||
    !FAILURE_CODE.test(value.code) ||
    !Number.isInteger(value.original_http_status) ||
    (value.original_http_status as number) < 400 ||
    (value.original_http_status as number) > 599
  ) {
    return false;
  }
  if (value.detail === null) return true;
  return (
    record(value.detail) &&
    exactKeys(value.detail, ["requires_new_quote"]) &&
    (value.detail.requires_new_quote === null || typeof value.detail.requires_new_quote === "boolean")
  );
}

export function parseBillingOperationLookup(value: unknown): BillingOperationLookup | null {
  if (
    !record(value) ||
    !exactKeys(value, LOOKUP_KEYS) ||
    !nonEmptyString(value.operation) ||
    typeof value.idempotency_key !== "string" ||
    !UUID.test(value.idempotency_key) ||
    !resultId(value.result_id)
  ) {
    return null;
  }
  const billing = parseBillingSummary(value.billing);
  if (!billing || billing.idempotency_key !== value.idempotency_key) return null;

  if (value.state === "in_progress") {
    if (
      value.completion_kind !== null ||
      billing.status !== "reserved" ||
      !(value.result_type === null || nonEmptyString(value.result_type)) ||
      !nullablePayload(value.resource) ||
      value.result !== null ||
      value.failure !== null
    ) {
      return null;
    }
  } else if (value.state === "completed" && value.completion_kind === "succeeded") {
    if (
      (billing.status !== "settled" && billing.status !== "partially_settled") ||
      !nonEmptyString(value.result_type) ||
      !record(value.result) ||
      !nullablePayload(value.resource) ||
      value.failure !== null
    ) {
      return null;
    }
  } else if (value.state === "completed" && value.completion_kind === "rejected") {
    if (
      billing.status !== "released" ||
      !nonEmptyString(value.result_type) ||
      !record(value.resource) ||
      value.result !== null ||
      value.failure !== null
    ) {
      return null;
    }
  } else if (value.state === "completed" && value.completion_kind === "failed") {
    if (
      billing.status !== "released" ||
      value.result_type !== null ||
      value.result_id !== null ||
      value.resource !== null ||
      value.result !== null ||
      !failure(value.failure)
    ) {
      return null;
    }
  } else {
    return null;
  }
  return value as unknown as BillingOperationLookup;
}

export async function getBillingOperation(
  operation: string,
  idempotencyKey: string
): Promise<BillingOperationLookup> {
  const value = await apiFetch<unknown>(
    `/api/v1/billing/operations/by-idempotency/${encodeURIComponent(operation)}/${encodeURIComponent(idempotencyKey)}`
  );
  const parsed = parseBillingOperationLookup(value);
  if (
    !parsed ||
    parsed.operation !== operation ||
    parsed.idempotency_key.toLowerCase() !== idempotencyKey.toLowerCase()
  ) {
    throw new ApiError("计费查询返回了无效数据。", "INVALID_BILLING_RESPONSE", 502);
  }
  return parsed;
}
