import type { AvatarDurationPolicy } from "../types";

/** Synthetic, non-signing fixture for the published wire; never a real credential. */
export function avatarPolicy(credits: number): AvatarDurationPolicy {
  return {
    version: "145s-no-refund-v1", max_seconds: 145,
    notice: "数字人视频最长支持145秒。若实际配音时长超过145秒导致生成失败，本次配音费和视频生成费均不退还。请在提交前确认文案与语速。",
    accepted_credits: credits, token: "synthetic-avatar-policy",
    expires_at: "2030-01-01T00:00:00Z"
  };
}
