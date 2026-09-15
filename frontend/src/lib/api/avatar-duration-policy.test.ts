import { describe, expect, it } from "vitest";
import { parseVideoEstimateContract } from "./videos";
import { parseBillingOperationLookup } from "./billing";
import { avatarFailureText, checkedAvatarBillingOutcome } from "./avatar-duration-policy";

const policy = {
  version: "145s-no-refund-v1", max_seconds: 145,
  notice: "数字人视频最长支持145秒。若实际配音时长超过145秒导致生成失败，本次配音费和视频生成费均不退还。请在提交前确认文案与语速。",
  accepted_credits: 123, token: "synthetic-policy-token", expires_at: "2030-01-01T00:00:00Z"
};
const estimate = { pricing_contract: "legacy_estimate", estimated_credits: 123, unit: "credits", avatar_duration_policy: policy };
const key = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const lookup = {
  operation: "video_create", idempotency_key: key, state: "completed", completion_kind: "failed_charged",
  billing: { operation_id: "op-1", idempotency_key: key, status: "settled", requested_credits: 123,
    held_credits: 0, settled_credits: 123, released_credits: 0 },
  result_type: null, result_id: null, result: null, resource: { task_id: "task-1", status: "failed" },
  failure: { code: "HEYGEN_AUDIO_DURATION_EXCEEDED", original_http_status: 422, detail: null }
};

describe("145s wire: quote binding and a narrow paid failure", () => {
  it("preserves server policy on a legacy estimate without inventing prices", () => {
    expect(parseVideoEstimateContract(estimate)).toEqual(estimate);
  });
  it.each([
    { version: "150s-v0" }, { max_seconds: 150 }, { accepted_credits: 124 },
    { accepted_credits: NaN }, { token: "" }, { expires_at: "not-a-date" }, { notice: "失败全退" }
  ])("rejects a mismatched/incomplete policy: %j", (change) => {
    expect(parseVideoEstimateContract({ ...estimate, avatar_duration_policy: { ...policy, ...change } })).toBeNull();
  });
  it.each([1, 123])("accepts a positive failed_charged lookup at %i credits", (credits) => {
    const positive = { ...lookup, billing: { ...lookup.billing, requested_credits: credits, settled_credits: credits } };
    expect(parseBillingOperationLookup(positive)).toEqual(positive);
  });
  // Only quote-backed video operations obey the DB positive-price domain.
  // A conserved zero summary alone cannot make this lookup legitimate.
  it("rejects a zero-credit video_create failed_charged lookup", () => {
    expect(parseBillingOperationLookup({ ...lookup,
      billing: { ...lookup.billing, requested_credits: 0, settled_credits: 0 }
    })).toBeNull();
  });
  it("preserves a legitimately quantized zero-credit legacy policy", () => {
    const zeroEstimate = { ...estimate, estimated_credits: 0,
      avatar_duration_policy: { ...policy, accepted_credits: 0 } };
    expect(parseVideoEstimateContract(zeroEstimate)).toEqual(zeroEstimate);
  });
  it("reads and displays a legacy zero-credit task failure without inventing a minimum", () => {
    const outcome = { completion_kind: "failed_charged", status: "settled", policy_version: "145s-no-refund-v1",
      requested_credits: 0, settled_credits: 0, released_credits: 0 };
    const read = { status: "failed", error_code: "HEYGEN_AUDIO_DURATION_EXCEEDED", billing_outcome: outcome };
    expect(checkedAvatarBillingOutcome(read)).toEqual(outcome);
    expect(avatarFailureText(read)).toBe("生成失败（超145秒，费用不退）。已结算 0 积分。");
  });
  it.each([
    { operation: "script_generate" }, { resource: { task_id: "task-1", status: "done" } },
    { failure: { code: "PROVIDER_FAILED", original_http_status: 422, detail: null } },
    { failure: { code: "HEYGEN_AUDIO_DURATION_EXCEEDED", original_http_status: 500, detail: null } },
    { completion_kind: "failed" }, { completion_kind: "succeeded" },
    { result: { task_id: "task-1", status: "done" } }, { resource: null },
    { billing: { ...lookup.billing, status: "released", settled_credits: 0, released_credits: 123 } }
  ])("refuses contradictions and widening ordinary failed to charged: %j", (change) => {
    expect(parseBillingOperationLookup({ ...lookup, ...change })).toBeNull();
  });
});
