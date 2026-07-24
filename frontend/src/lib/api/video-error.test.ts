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

  // V2V-FIX1（#216 真联调）：审核拒（含真人）是任务执行期异步失败 → 走本映射；curated 文案含「未扣积分」说明
  // （SPIKE 实测 credits_cost=0），比 BE message 更完整。变异：从映射表删该码 → 本条红（落 videoGeneric 丢未扣费信息）。
  it("VIDEO_REFERENCE_CONTENT_REJECTED → 审核拒文案（含不扣积分说明），不落通用兜底", () => {
    const out = friendlyVideoError("VIDEO_REFERENCE_CONTENT_REJECTED");
    expect(out).toBe(copy.workbench.vgVideoModerationRejected);
    expect(out).toContain("未扣除积分");
    expect(out).not.toBe(copy.errors.videoGeneric);
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
