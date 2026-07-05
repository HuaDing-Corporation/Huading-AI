import { describe, expect, it } from "vitest";

import { copy } from "@/lib/copy";
import { friendlyVideoError } from "./video-error";

// VIDEO-ERR-MAP-UI：四码 → 友好中文；未知/缺失 → 通用兜底；**绝不回落裸 error_message/技术串**。
describe("friendlyVideoError (视频失败友好映射)", () => {
  it("四个已知码各映射到对应中文文案（与后端 VIDEO-ERR-MAP-BE 共用码）", () => {
    expect(friendlyVideoError("VIDEO_INSUFFICIENT_BALANCE")).toBe(copy.errors.videoInsufficientBalance);
    expect(friendlyVideoError("VIDEO_TIMEOUT")).toBe(copy.errors.videoTimeout);
    expect(friendlyVideoError("VIDEO_CONNECTION_ERROR")).toBe(copy.errors.videoConnection);
    expect(friendlyVideoError("VIDEO_GEN_FAILED")).toBe(copy.errors.videoGeneric);
  });

  it("未知码 → 通用中文兜底（不返回码本身）", () => {
    expect(friendlyVideoError("SOME_UNKNOWN_CODE")).toBe(copy.errors.videoGeneric);
  });

  it("缺失码（undefined/null）→ 通用中文兜底", () => {
    expect(friendlyVideoError()).toBe(copy.errors.videoGeneric);
    expect(friendlyVideoError(null)).toBe(copy.errors.videoGeneric);
  });

  it("承重·契约硬：真传裸串 fallback，未知码也绝不吐裸串（回落 videoGeneric，非仅靠调用方约定）", () => {
    // 已知码：忽略 fallback，返回友好文案。
    expect(friendlyVideoError("VIDEO_TIMEOUT", "Error code: 402 - {raw json}")).toBe(copy.errors.videoTimeout);
    // 关键承重：未知码 + 真裸串 fallback（英文/JSON/栈帧）→ 函数级硬保证丢弃裸串、回落 videoGeneric。
    for (const raw of ["Error code: 504 - {raw json}", "RuntimeError: boom", "at foo (bar.js:1:2)", "https://provider/err"]) {
      const out = friendlyVideoError("UNKNOWN_CODE", raw);
      expect(out).toBe(copy.errors.videoGeneric);
      expect(out).not.toBe(raw);
    }
    // 安全中文 fallback 仍可用（非技术串不误伤）。
    expect(friendlyVideoError("UNKNOWN_CODE", copy.errors.generic)).toBe(copy.errors.generic);
  });

  it("批量视频失败码 BATCH_IMAGE_DOWNLOAD_FAILED → 可操作中文（批量详情复用）", () => {
    expect(friendlyVideoError("BATCH_IMAGE_DOWNLOAD_FAILED")).toBe(copy.errors.batchImageDownloadFailed);
  });
});
