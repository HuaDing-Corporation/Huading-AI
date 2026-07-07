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
});
