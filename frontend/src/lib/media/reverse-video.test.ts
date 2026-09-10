import { describe, expect, it } from "vitest";

import { copy } from "@/lib/copy";
import {
  MAX_REVERSE_VIDEO_SEC,
  MIN_REVERSE_VIDEO_SEC,
  REVERSE_VIDEO_DURATION_TOLERANCE,
  validateReverseVideoFile,
  validateReverseVideoMetadata
} from "./reverse-video";

const file = (type: string, sizeMB = 1): File => {
  const f = new File([""], "v", { type });
  Object.defineProperty(f, "size", { value: sizeMB * 1024 * 1024 }); // jsdom File.size 只读 → defineProperty 模拟大小
  return f;
};
const meta = (duration: number) => ({ duration, width: 1080, height: 1920 });

/**
 * REVERSE-DEEP-UI-0001-FIX1 · 承重门13：**D2 把反推视频时长上限从 60 秒放宽到 180 秒**（§八 8.3 D9 那轮同批拍板，
 * 依据 §八 8.1 SPIKE 实测：180s 走方案 A = 6 段 × 30 秒 × 6 帧，端到端 198.312s、时间轴 100% 覆盖）。
 *
 * 🔴 本文件是这次放宽的承重面。上一轮这条链路**根本没有单测**（只有 form 里一条「预检不合规→显示友好文案」的
 *    间接用例，它 mock 掉了 validateReverseVideo 本身）——也就是说 60→180 这个数改错了也没人会红。
 *    故此处直接钉边界值与文案，两侧都断。
 */
describe("reverse-video 预检（VIDEO-REVERSE-PROMPT-UI-0001 · D2 放宽到 180s）", () => {
  it("类型/大小：MP4 通过；非 MP4 与 >200MB 拒", () => {
    expect(validateReverseVideoFile(file("video/mp4", 200))).toBeNull();
    expect(validateReverseVideoFile(file("video/quicktime"))).toBe(copy.errors.videoType);
    expect(validateReverseVideoFile(file("video/mp4", 201))).toBe(copy.errors.videoTooLarge);
  });

  it("🔴 承重门13 · 时长上限 = 180 秒：180s 通过；181s 之外（超出容差）拒", () => {
    expect(MAX_REVERSE_VIDEO_SEC).toBe(180);
    expect(validateReverseVideoMetadata(meta(180))).toBeNull();
    // 容差是给元数据抖动的，不是给「多一秒也行」的 → 越过 180+容差必须拒。
    expect(validateReverseVideoMetadata(meta(MAX_REVERSE_VIDEO_SEC + REVERSE_VIDEO_DURATION_TOLERANCE + 0.1))).toBe(
      copy.errors.reverseVideoDuration
    );
  });

  it("时长下限 = 1 秒：1s 通过；0.1s 拒；读不到元数据 → 不可读文案", () => {
    expect(MIN_REVERSE_VIDEO_SEC).toBe(1);
    expect(validateReverseVideoMetadata(meta(1))).toBeNull();
    expect(validateReverseVideoMetadata(meta(0.1))).toBe(copy.errors.reverseVideoDuration);
    expect(validateReverseVideoMetadata(meta(0))).toBe(copy.errors.videoUnreadable);
    expect(validateReverseVideoMetadata(meta(Number.NaN))).toBe(copy.errors.videoUnreadable);
  });

  it("🔴 承重门13 · 时长文案仍写 180 秒、且不再出现旧 1–60 秒范围", () => {
    // 界面上用户能读到的两处（上传区提示 + 预检错误），必须与常量同口径。
    expect(copy.reverse.videoUploadHint).toContain("180");
    expect(copy.errors.reverseVideoDuration).toContain("180");
    // 🔴 「不再出现 60」是这条承重门的原话：只断「含 180」的话，把文案写成「1–60 秒（最长 180）」也能绿。
    // Match the obsolete duration, not the unrelated 2160-pixel upper bound.
    expect(copy.reverse.videoUploadHint).not.toMatch(/1[–-]60\s*秒/);
    expect(copy.errors.reverseVideoDuration).not.toMatch(/1[–-]60\s*秒/);
    // 常量与文案同源：改了常量却忘了改文案（或反之）→ 本条红。
    expect(copy.reverse.videoUploadHint).toContain(String(MAX_REVERSE_VIDEO_SEC));
    expect(copy.errors.reverseVideoDuration).toContain(String(MAX_REVERSE_VIDEO_SEC));
  });

  it("同形不同类的 60 不受牵连：OmniHuman 口播脚本上限仍是 60 秒（不是反推时长）", async () => {
    // ⚠️ 全类 grep 时最容易误伤的一条：lib/sse/constants.ts 的 MAX_SCRIPT_SECONDS 是**数字人口播音频**上限，
    //    与反推视频时长毫无关系。此处显式钉住，防止下一轮有人「顺手一起改成 180」。
    const { MAX_SCRIPT_SECONDS } = await import("@/lib/sse/constants");
    expect(MAX_SCRIPT_SECONDS).toBe(60);
  });

  it.each([[240, 240], [240, 2160], [2160, 240], [2160, 2160], [1920, 1080], [1080, 1920]])(
    "accepts BE-valid %i × %i pixels", (width, height) => {
      expect(validateReverseVideoMetadata({ duration: 5, width, height })).toBeNull();
    }
  );

  it.each([[239, 2160], [2160, 239], [240, 2161], [2161, 240], [3840, 2160], [0, 720]])(
    "rejects BE-invalid %i × %i pixels with the specific resolution guidance", (width, height) => {
      expect(validateReverseVideoMetadata({ duration: 5, width, height })).toEqual(expect.stringMatching(/短边.*240.*长边.*2160/));
    }
  );

  it.each([[Number.NaN, 720], [720, Number.NaN], [Infinity, 720], [720, Infinity]])(
    "rejects unreadable dimensions %s × %s", (width, height) => {
      expect(validateReverseVideoMetadata({ duration: 5, width, height })).toBe(copy.errors.videoUnreadable);
    }
  );

  it("retains the existing duration tolerance at both edges", () => {
    expect(validateReverseVideoMetadata(meta(0.5))).toBeNull();
    expect(validateReverseVideoMetadata(meta(180.5))).toBeNull();
    expect(validateReverseVideoMetadata(meta(0.49))).toBe(copy.errors.reverseVideoDuration);
    expect(validateReverseVideoMetadata(meta(180.51))).toBe(copy.errors.reverseVideoDuration);
  });

  it("keeps the exact 209715200-byte reverse cap", () => {
    expect(validateReverseVideoFile(file("video/mp4", 200))).toBeNull();
    const oversize = new File(["x"], "large.mp4", { type: "video/mp4" });
    Object.defineProperty(oversize, "size", { value: 209715201 });
    expect(validateReverseVideoFile(oversize)).toBe(copy.errors.videoTooLarge);
  });
});
