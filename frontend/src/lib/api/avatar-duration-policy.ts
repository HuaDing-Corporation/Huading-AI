import type { AvatarBillingOutcome, AvatarDurationPolicy, CreateVideoRequest, VideoEstimateContract } from "./types";
import { ApiError } from "./client";
import { copy } from "@/lib/copy";

/** Mirrors published HEYGEN-145-NOREFUND wire v1; never infers consent from an estimate. */
export function isHeygenAvatarRequest(request: CreateVideoRequest | null): boolean {
  return !!request && (request.video_mode === "avatar_talk" ||
    (!request.video_mode && !!(request.avatar_asset_id || request.avatar_video_asset_id)));
}

export function parseAvatarDurationPolicy(value: unknown, credits: number): AvatarDurationPolicy | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const data = value as Record<string, unknown>;
  const keys = ["version", "max_seconds", "notice", "accepted_credits", "token", "expires_at"];
  if (Object.keys(data).length !== keys.length || keys.some((key) => !Object.hasOwn(data, key))) return null;
  return data.version === "145s-no-refund-v1" && data.max_seconds === 145 &&
    data.notice === copy.confirm.avatarDurationPolicy &&
    Number.isSafeInteger(data.accepted_credits) && credits >= 0 && data.accepted_credits === credits &&
    typeof data.token === "string" && data.token.trim().length > 0 &&
    typeof data.expires_at === "string" && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(data.expires_at) &&
    Number.isFinite(Date.parse(data.expires_at)) ? data as unknown as AvatarDurationPolicy : null;
}

export function policyForEstimate(estimate: VideoEstimateContract | null): AvatarDurationPolicy | null {
  if (!estimate || estimate.pricing_contract === "deferred_unpriced") return null;
  return parseAvatarDurationPolicy(estimate.avatar_duration_policy,
    estimate.pricing_contract === "billing_quote" ? estimate.payable_credits : estimate.estimated_credits);
}

export function checkedAvatarBillingOutcome(read: {
  status?: string; error_code?: string | null; billing_outcome?: unknown;
}): AvatarBillingOutcome | null {
  const value = read.billing_outcome;
  if (value === undefined || value === null) return null;
  if (typeof value === "object" && !Array.isArray(value)) {
    const data = value as Record<string, unknown>;
    const keys = ["completion_kind", "status", "policy_version", "requested_credits", "settled_credits", "released_credits"];
    if (read.status === "failed" && read.error_code === "HEYGEN_AUDIO_DURATION_EXCEEDED" &&
      Object.keys(data).length === keys.length && keys.every((key) => Object.hasOwn(data, key)) &&
      data.completion_kind === "failed_charged" && data.status === "settled" && data.policy_version === "145s-no-refund-v1" &&
      Number.isSafeInteger(data.requested_credits) && (data.requested_credits as number) >= 0 &&
      data.settled_credits === data.requested_credits && data.released_credits === 0) return data as unknown as AvatarBillingOutcome;
  }
  throw new ApiError("任务计费状态尚无法确认，请重新查询原任务。", "INVALID_AVATAR_BILLING_OUTCOME", 502);
}

export function avatarFailureText(read: { status?: string; error_code?: string | null; billing_outcome?: unknown }): string | null {
  const outcome = checkedAvatarBillingOutcome(read);
  if (outcome) return `生成失败（超145秒，费用不退）。已结算 ${outcome.settled_credits} 积分。`;
  return read.status === "failed" && read.error_code === "HEYGEN_AUDIO_DURATION_EXCEEDED"
    ? "生成失败（实际配音超过145秒）。计费结果尚未确认，请查询原任务或用量记录。" : null;
}
