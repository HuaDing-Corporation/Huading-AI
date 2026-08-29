import { apiFetch, ApiError, isApiError } from "./client";
import type {
  BillingConfirmation,
  BillingLookupPayload,
  BillingOperationLookup,
  BillingQuote,
  BillingSummary,
  ExtendedBillingOperationLookup
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

function nullableString(value: unknown): value is string | null {
  return value === null || nonEmptyString(value);
}

function nullableIsoDate(value: unknown): value is string | null {
  return value === null || isoDate(value);
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

export interface BillingLookupResultSchema {
  operations: readonly string[];
  completionKinds: readonly ("succeeded" | "rejected")[];
  parse: (value: unknown) => BillingLookupPayload | null;
  validateResultId: (resultId: string | null, payload: BillingLookupPayload | null) => boolean;
  validateContext?: (
    payload: BillingLookupPayload,
    lookup: { billing: BillingSummary; completionKind: "succeeded" | "rejected" | null }
  ) => boolean;
}

export type BillingLookupResultRegistry = Readonly<Record<string, BillingLookupResultSchema>>;

function exactPayload(
  value: unknown,
  keys: readonly string[],
  validate: (payload: Record<string, unknown>) => boolean
): BillingLookupPayload | null {
  return record(value) && exactKeys(value, keys) && validate(value) ? value : null;
}

function parseScriptResult(value: unknown): BillingLookupPayload | null {
  return exactPayload(value, ["script"], (payload) => typeof payload.script === "string");
}

function parseScenePromptResult(value: unknown): BillingLookupPayload | null {
  return exactPayload(value, ["scene_prompt", "negative_prompt"], (payload) =>
    typeof payload.scene_prompt === "string" && typeof payload.negative_prompt === "string"
  );
}

const ECOM_ITEM_KEYS = [
  "item_index",
  "task_id",
  "source_asset_id",
  "status",
  "asset_id"
] as const;

function parseEcomImageBatch(value: unknown): BillingLookupPayload | null {
  return exactPayload(value, ["items"], (payload) => {
    if (!Array.isArray(payload.items) || payload.items.length < 1 || payload.items.length > 20) return false;
    const indexes = new Set<number>();
    for (const item of payload.items) {
      if (
        !record(item) ||
        !exactKeys(item, ECOM_ITEM_KEYS) ||
        !nonNegativeInteger(item.item_index) ||
        item.item_index > 19 ||
        indexes.has(item.item_index) ||
        !nonEmptyString(item.task_id) ||
        item.task_id.length > 36 ||
        !nonEmptyString(item.source_asset_id) ||
        item.source_asset_id.length > 36 ||
        (item.status !== "done" && item.status !== "failed") ||
        !(item.asset_id === null || (nonEmptyString(item.asset_id) && item.asset_id.length <= 36))
      ) {
        return false;
      }
      indexes.add(item.item_index);
    }
    return [...indexes].sort((a, b) => a - b).every((index, position) => index === position);
  });
}

function parseVideoTask(value: unknown): BillingLookupPayload | null {
  return exactPayload(value, ["task_id", "status"], (payload) =>
    nonEmptyString(payload.task_id) &&
    payload.task_id.length <= 36 &&
    ["queued", "running", "done", "failed", "cancelled"].includes(payload.status as string)
  );
}

const BRAND_VOICE_KEYS = [
  "id",
  "name",
  "provider",
  "status",
  "order_status",
  "delivery_status",
  "created_at"
] as const;

function parseBrandVoice(value: unknown): BillingLookupPayload | null {
  return exactPayload(value, BRAND_VOICE_KEYS, (payload) =>
    nonEmptyString(payload.id) &&
    nonEmptyString(payload.name) &&
    nonEmptyString(payload.provider) &&
    nonEmptyString(payload.status) &&
    (payload.order_status === null ||
      ["awaiting_fulfillment", "fulfilled", "rejected"].includes(payload.order_status as string)) &&
    ["awaiting_fulfillment", "active", "expired", "rejected"].includes(
      payload.delivery_status as string
    ) &&
    isoDate(payload.created_at)
  );
}

const BRAND_VOICE_ORDER_KEYS = [
  "id",
  "tenant_id",
  "ordered_by_user_id",
  "order_type",
  "requested_name",
  "source_audio_asset_id",
  "existing_brand_voice_id",
  "status",
  "fulfilled_brand_voice_id",
  "fulfilled_provider_voice_id",
  "rejection_reason",
  "fulfilled_at",
  "rejected_at",
  "created_at",
  "updated_at",
  "billing",
  "refund_disposition",
  "refund_grant_status",
  "refund_applied_at"
] as const;

function parseBrandVoiceOrder(value: unknown): BillingLookupPayload | null {
  return exactPayload(value, BRAND_VOICE_ORDER_KEYS, (payload) => {
    const billing = parseBillingSummary(payload.billing);
    const status = payload.status;
    const common =
      nonEmptyString(payload.id) &&
      nonEmptyString(payload.tenant_id) &&
      nonEmptyString(payload.ordered_by_user_id) &&
      (payload.order_type === "create" || payload.order_type === "renew") &&
      nonEmptyString(payload.requested_name) &&
      nonEmptyString(payload.source_audio_asset_id) &&
      nullableString(payload.existing_brand_voice_id) &&
      (payload.order_type !== "create" || payload.existing_brand_voice_id === null) &&
      (payload.order_type !== "renew" || nonEmptyString(payload.existing_brand_voice_id)) &&
      nullableString(payload.fulfilled_brand_voice_id) &&
      nullableString(payload.fulfilled_provider_voice_id) &&
      nullableString(payload.rejection_reason) &&
      nullableIsoDate(payload.fulfilled_at) &&
      nullableIsoDate(payload.rejected_at) &&
      isoDate(payload.created_at) &&
      isoDate(payload.updated_at) &&
      billing !== null &&
      [
        "not_applicable",
        "source_subscription_released",
        "current_subscription_credited",
        "pending_next_subscription"
      ].includes(payload.refund_disposition as string) &&
      (payload.refund_grant_status === null ||
        payload.refund_grant_status === "pending" ||
        payload.refund_grant_status === "applied") &&
      nullableIsoDate(payload.refund_applied_at);
    if (!common) return false;
    if (status === "awaiting_fulfillment") {
      return (
        payload.fulfilled_brand_voice_id === null &&
        payload.fulfilled_provider_voice_id === null &&
        payload.rejection_reason === null &&
        payload.fulfilled_at === null &&
        payload.rejected_at === null
      );
    }
    if (status === "fulfilled") {
      return (
        nonEmptyString(payload.fulfilled_brand_voice_id) &&
        nonEmptyString(payload.fulfilled_provider_voice_id) &&
        payload.rejection_reason === null &&
        isoDate(payload.fulfilled_at) &&
        payload.rejected_at === null
      );
    }
    return (
      status === "rejected" &&
      payload.fulfilled_brand_voice_id === null &&
      payload.fulfilled_provider_voice_id === null &&
      nonEmptyString(payload.rejection_reason) &&
      payload.fulfilled_at === null &&
      isoDate(payload.rejected_at)
    );
  });
}

function payloadId(field: string) {
  return (resultId: string | null, payload: BillingLookupPayload | null): boolean =>
    payload !== null && nonEmptyString(resultId) && payload[field] === resultId;
}

export const defaultBillingLookupResultRegistry: BillingLookupResultRegistry = {
  script_generate_result: {
    operations: ["script_generate"],
    completionKinds: ["succeeded"],
    parse: parseScriptResult,
    validateResultId: (resultId) => resultId === null
  },
  scene_prompt_result: {
    operations: ["scene_prompt"],
    completionKinds: ["succeeded"],
    parse: parseScenePromptResult,
    validateResultId: (resultId) => resultId === null
  },
  ecom_image_batch: {
    operations: ["ecom_cutout", "ecom_model"],
    completionKinds: ["succeeded"],
    parse: parseEcomImageBatch,
    validateResultId: (resultId) => nonEmptyString(resultId) && UUID.test(resultId)
  },
  video_task: {
    operations: ["video_create"],
    completionKinds: ["succeeded"],
    parse: parseVideoTask,
    validateResultId: payloadId("task_id")
  },
  brand_voice_order: {
    operations: ["doubao_brand_voice_order_create", "doubao_brand_voice_order_renew"],
    completionKinds: ["succeeded", "rejected"],
    parse: parseBrandVoiceOrder,
    validateResultId: payloadId("id"),
    validateContext: (payload, lookup) => {
      const nestedBilling = parseBillingSummary(payload.billing);
      if (
        !nestedBilling ||
        nestedBilling.operation_id !== lookup.billing.operation_id ||
        nestedBilling.idempotency_key !== lookup.billing.idempotency_key
      ) {
        return false;
      }
      return lookup.completionKind === "rejected"
        ? payload.status === "rejected"
        : payload.status === "fulfilled" || payload.status === "awaiting_fulfillment";
    }
  },
  brand_voice: {
    operations: ["cosyvoice_brand_voice_create"],
    completionKinds: ["succeeded"],
    parse: parseBrandVoice,
    validateResultId: payloadId("id")
  }
};

function payloadsEqual(left: BillingLookupPayload, right: BillingLookupPayload): boolean {
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  if (leftKeys.length !== rightKeys.length || leftKeys.some((key, index) => key !== rightKeys[index])) {
    return false;
  }
  return leftKeys.every((key) => {
    const a = left[key];
    const b = right[key];
    if (record(a) && record(b)) return payloadsEqual(a, b);
    if (Array.isArray(a) && Array.isArray(b)) {
      return a.length === b.length && a.every((item, index) => {
        const other = b[index];
        return record(item) && record(other) ? payloadsEqual(item, other) : item === other;
      });
    }
    return a === b;
  });
}

export function parseBillingOperationLookup(value: unknown): BillingOperationLookup | null;
export function parseBillingOperationLookup(
  value: unknown,
  registry: BillingLookupResultRegistry
): ExtendedBillingOperationLookup | null;
export function parseBillingOperationLookup(
  value: unknown,
  registry: BillingLookupResultRegistry = defaultBillingLookupResultRegistry
): BillingOperationLookup | ExtendedBillingOperationLookup | null {
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
  const schema = typeof value.result_type === "string" ? registry[value.result_type] : null;
  const knownOperation = Object.values(registry).some((entry) =>
    entry.operations.includes(value.operation as string)
  );
  if (!knownOperation) return null;
  if (value.result_type !== null && !schema) return null;
  if (schema && !schema.operations.includes(value.operation)) return null;

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
    if (value.result_type === null && (value.result_id !== null || value.resource !== null)) return null;
    if (schema) {
      const resource = value.resource === null ? null : schema.parse(value.resource);
      if (value.resource !== null && !resource) return null;
      if (!schema.validateResultId(value.result_id as string | null, resource)) return null;
      if (
        resource &&
        schema.validateContext &&
        !schema.validateContext(resource, { billing, completionKind: null })
      ) {
        return null;
      }
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
    if (!schema || !schema.completionKinds.includes("succeeded")) return null;
    const result = schema.parse(value.result);
    if (!result || !schema.validateResultId(value.result_id as string | null, result)) return null;
    if (value.result_id === null) {
      if (value.resource !== null) return null;
    } else {
      const resource = schema.parse(value.resource);
      if (!resource || !payloadsEqual(result, resource)) return null;
    }
    if (
      schema.validateContext &&
      !schema.validateContext(result, { billing, completionKind: "succeeded" })
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
    if (!schema || !schema.completionKinds.includes("rejected")) return null;
    const resource = schema.parse(value.resource);
    if (!resource || !schema.validateResultId(value.result_id as string | null, resource)) return null;
    if (
      schema.validateContext &&
      !schema.validateContext(resource, { billing, completionKind: "rejected" })
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
  return value as unknown as BillingOperationLookup | ExtendedBillingOperationLookup;
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
