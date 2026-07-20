import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api/client";
import { copy } from "@/lib/copy";
import { errorText } from "./error-text";

// FE-INTEGRATION-0001：数字人出镜视频源·后端二次校验码 → 友好中文回显（前端预检读不到编码/音轨，靠 BE 权威码）。
describe("errorText · AVATAR_VIDEO_* 后端校验码 → 友好中文", () => {
  const cases: [string, string][] = [
    ["AVATAR_VIDEO_UNSUPPORTED_FORMAT", copy.errors.videoType],
    ["AVATAR_VIDEO_TOO_LARGE", copy.errors.videoTooLarge],
    ["AVATAR_VIDEO_DURATION_INVALID", copy.errors.videoTooLong],
    ["AVATAR_VIDEO_RESOLUTION_INVALID", copy.errors.videoResolution],
    ["AVATAR_VIDEO_CODEC_INVALID", copy.errors.videoCodec],
    ["AVATAR_VIDEO_AUDIO_CODEC_INVALID", copy.errors.videoAudioCodec],
    ["AVATAR_VIDEO_DECODE_FAILED", copy.errors.videoUnreadable],
    ["AVATAR_VIDEO_NOT_READABLE", copy.errors.videoUnreadable],
    ["AVATAR_VIDEO_ASSET_NOT_FOUND", copy.errors.videoAssetLost]
  ];
  for (const [code, expected] of cases) {
    it(`${code} → 友好中文，不露后端英文 message`, () => {
      const out = errorText(new ApiError("Avatar source video must include AAC audio.", code, 422));
      expect(out).toBe(expected);
      expect(out).not.toContain("Avatar source video");
    });
  }

  it("未知码 → 透后端 message（既有行为不变）", () => {
    expect(errorText(new ApiError("Active subscription not found.", "subscription_not_found", 404))).toBe(
      "Active subscription not found."
    );
  });

  // ADMIN-VIP-GATE-UI-0001 §二之二：VIP 门禁码 → 友好中文，不透传英文串（与「槽位空」区分）。
  it("VOICE_CLONE_PLAN_REQUIRED → 友好中文，不露后端英文 message", () => {
    const out = errorText(new ApiError("Voice clone (doubao) requires the huading plan.", "VOICE_CLONE_PLAN_REQUIRED", 403));
    expect(out).toBe(copy.errors.voiceClonePlanRequired);
    expect(out).not.toMatch(/huading plan\.$/);
  });
});

// IMAGE-GEN-OPTIMIZE-UI-0001-FIX1：图片服务能力 422（BE providers/base.py fail-closed，落钱前拦）→ 优先透 BE 动态友好中文
// （如 OpenAI provider 选 2K → 「当前图片服务不支持 2K，请选择 1K。」），不落通用「操作失败」；BE message 空时才兜底 curated。
// VIDEO-GEN-PARAMS-UI-0001-FIX1（#213 真联调）：2000 墙 BE 权威分流——friendly_video_gen_prompt_too_long →
// code=VIDEO_GEN_PROMPT_TOO_LONG，message 与前端红字一字不差；空 message 兜底同句文案，不落通用「操作失败」。
describe("errorText · VIDEO_GEN_PROMPT_TOO_LONG（FIX1 真联调）", () => {
  it("透 BE message（与前端红字同句）", () => {
    const msg = "提示词输入最大上限为 2000 字";
    expect(errorText(new ApiError(msg, "VIDEO_GEN_PROMPT_TOO_LONG", 422))).toBe(msg);
  });
  it("message 意外为空 → 兜底同句红字文案，不落通用报错", () => {
    const out = errorText(new ApiError("", "VIDEO_GEN_PROMPT_TOO_LONG", 422));
    expect(out).toBe(copy.workbench.vgPromptOverLimit);
    expect(out).not.toBe(copy.errors.generic);
  });
});

describe("errorText · IMAGE_PROVIDER_* 能力码（FIX1 真联调）", () => {
  const codes = [
    "IMAGE_PROVIDER_RESOLUTION_UNSUPPORTED",
    "IMAGE_PROVIDER_REFERENCE_IMAGES_UNSUPPORTED",
    "IMAGE_PROVIDER_CAPABILITIES_UNDECLARED"
  ];
  it("有 BE 动态 message → 原样透出（比前端静态文案更精确），不落通用兜底", () => {
    const msg = "当前图片服务不支持 2K，请选择 1K。"; // 逐字对齐 base.py 的 user_message
    const out = errorText(new ApiError(msg, "IMAGE_PROVIDER_RESOLUTION_UNSUPPORTED", 422));
    expect(out).toBe(msg);
    expect(out).not.toBe(copy.errors.generic);
  });
  it("多图越 provider 上限 → 透 BE「最多支持 N 张」", () => {
    const msg = "当前图片服务最多支持 1 张参考图。";
    expect(errorText(new ApiError(msg, "IMAGE_PROVIDER_REFERENCE_IMAGES_UNSUPPORTED", 422))).toBe(msg);
  });
  for (const code of codes) {
    it(`${code} 且 message 意外为空 → 兜底 curated 文案，绝不落通用「操作失败」`, () => {
      const out = errorText(new ApiError("", code, 422));
      expect(out).toBe(copy.errors.imageProviderCapability);
      expect(out).not.toBe(copy.errors.generic);
    });
  }
});
