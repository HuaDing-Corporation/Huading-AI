import { describe, expect, it } from "vitest";

import { copy } from "@/lib/copy";
import {
  MAX_AVATAR_VIDEO_BYTES,
  validateAvatarVideoFile,
  validateAvatarVideoMetadata
} from "./avatar-video";

// AVATAR-VIDEO-SOURCE-UI-0001 预检拒绝矩阵（纯函数确定性）。FE-INTEGRATION-0001 对齐 BE 定稿：
// MP4 / ≤200MB / 时长 3–10s(含 0.5 容差) / 分辨率短边≥360 且长边≤1920。
function typedFile(type: string, name = "v", size = 1024): File {
  const f = new File([new Uint8Array(8)], name, { type });
  Object.defineProperty(f, "size", { value: size });
  return f;
}
const mp4 = (size = 1024) => typedFile("video/mp4", "v.mp4", size);

describe("validateAvatarVideoFile（MIME + 大小）", () => {
  it("合法 MP4（限额内）→ null", () => {
    expect(validateAvatarVideoFile(mp4(1024))).toBeNull();
  });
  it("承重·不信文件名：伪装 .mp4 但真实 MIME 非 mp4 → 类型错误", () => {
    const disguised = new File([new Uint8Array(8)], "evil.mp4", { type: "video/quicktime" });
    expect(validateAvatarVideoFile(disguised)).toBe(copy.errors.videoType);
  });
  it("非 MP4（webm/mov）→ 类型错误", () => {
    expect(validateAvatarVideoFile(typedFile("video/webm"))).toBe(copy.errors.videoType);
  });
  it("超过体积上限 → 过大错误", () => {
    expect(validateAvatarVideoFile(mp4(MAX_AVATAR_VIDEO_BYTES + 1))).toBe(copy.errors.videoTooLarge);
  });
});

describe("validateAvatarVideoMetadata（时长 3–10s + 分辨率 短边≥360 & 长边≤1920）", () => {
  it("合规：8s / 720×1280(短边720、长边1280) → null", () => {
    expect(validateAvatarVideoMetadata({ duration: 8, width: 720, height: 1280 })).toBeNull();
  });
  it("时长上界：10.4s（10+容差内）通过；10.6s 超时长 → videoTooLong", () => {
    expect(validateAvatarVideoMetadata({ duration: 10.4, width: 720, height: 1280 })).toBeNull();
    expect(validateAvatarVideoMetadata({ duration: 10.6, width: 720, height: 1280 })).toBe(copy.errors.videoTooLong);
  });
  it("时长下界(FE-INTEGRATION 对齐 3s)：2.4s 太短 → videoTooLong；3s 通过", () => {
    expect(validateAvatarVideoMetadata({ duration: 2.4, width: 720, height: 1280 })).toBe(copy.errors.videoTooLong);
    expect(validateAvatarVideoMetadata({ duration: 3, width: 720, height: 1280 })).toBeNull();
  });
  it("分辨率短边 < 360（如 320×568）→ videoResolution", () => {
    expect(validateAvatarVideoMetadata({ duration: 5, width: 320, height: 568 })).toBe(copy.errors.videoResolution);
  });
  it("分辨率长边 > 1920（如 1440×2560）→ videoResolution", () => {
    expect(validateAvatarVideoMetadata({ duration: 5, width: 1440, height: 2560 })).toBe(copy.errors.videoResolution);
  });
  // 承重·对齐 BE(min≥360 && max≤1920)，非旧「短边≤1080」：
  it("长边>1920 即拒(800×2000，旧短边规则会误放行) → videoResolution", () => {
    expect(validateAvatarVideoMetadata({ duration: 5, width: 800, height: 2000 })).toBe(copy.errors.videoResolution);
  });
  it("短边>1080 但长边≤1920 应通过(1200×1300，旧短边规则会误拒)", () => {
    expect(validateAvatarVideoMetadata({ duration: 5, width: 1200, height: 1300 })).toBeNull();
  });
  it("边界短边=360 与 长边=1920 均通过", () => {
    expect(validateAvatarVideoMetadata({ duration: 5, width: 360, height: 640 })).toBeNull();
    expect(validateAvatarVideoMetadata({ duration: 5, width: 1080, height: 1920 })).toBeNull();
  });
  it("横屏 1280×720（短边720）通过（兼容横竖）", () => {
    expect(validateAvatarVideoMetadata({ duration: 5, width: 1280, height: 720 })).toBeNull();
  });
  it("元数据不可读（duration=0 / NaN / 无宽高）→ videoUnreadable", () => {
    expect(validateAvatarVideoMetadata({ duration: 0, width: 720, height: 1280 })).toBe(copy.errors.videoUnreadable);
    expect(validateAvatarVideoMetadata({ duration: NaN, width: 720, height: 1280 })).toBe(copy.errors.videoUnreadable);
    expect(validateAvatarVideoMetadata({ duration: 5, width: 0, height: 0 })).toBe(copy.errors.videoUnreadable);
  });
});
