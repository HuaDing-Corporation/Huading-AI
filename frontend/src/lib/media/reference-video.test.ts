import { describe, expect, it } from "vitest";

import { copy } from "@/lib/copy";
import {
  inspectReferenceVideoMetadata,
  totalDurationStatus,
  validateReferenceVideoFile
} from "./reference-video";

const file = (type: string, sizeMB = 1): File => {
  const f = new File([""], "v", { type });
  Object.defineProperty(f, "size", { value: sizeMB * 1024 * 1024 }); // jsdom File.size 只读 → defineProperty 模拟大小
  return f;
};

// VIDEO-GEN-V2V-UI-0001 预检纯函数（D9/D10 契约）：MP4/MOV/WEBM、≤100MB、单条 >15.2s 拒（不代剪）、
// 短边 <480p 拒、>720p 通过但标记降码告知、MOV/WEBM 标记转码告知；合计 1.8–15.2 联动三态。
describe("reference-video 预检（V2V D9/D10）", () => {
  it("类型：MP4/MOV/WEBM 通过；GIF/AVI 拒", () => {
    expect(validateReferenceVideoFile(file("video/mp4"))).toBeNull();
    expect(validateReferenceVideoFile(file("video/quicktime"))).toBeNull();
    expect(validateReferenceVideoFile(file("video/webm"))).toBeNull();
    expect(validateReferenceVideoFile(file("image/gif"))).toBe(copy.workbench.vgRefVideoType);
    expect(validateReferenceVideoFile(file("video/x-msvideo"))).toBe(copy.workbench.vgRefVideoType);
  });

  it("大小：100MB 通过；101MB 拒（D9 表）", () => {
    expect(validateReferenceVideoFile(file("video/mp4", 100))).toBeNull();
    expect(validateReferenceVideoFile(file("video/mp4", 101))).toBe(copy.workbench.vgRefVideoTooLarge);
  });

  // FIX1（#216 真联调）：单条=闭区间 [1.8,15.2]（video_reference.py:128-137 `< MIN or > MAX`）——过短也拦
  // （原前端无下限+上限 0.2 容差 → 1.0s/15.3s 都「前端放过、BE 422」，已改硬边界）。
  it("单条时长闭区间：恰 1.8/15.2 通过；1.0s 拒（过短）；15.3s 拒（无容差，D9 不代剪）", () => {
    const mp4 = file("video/mp4");
    expect(inspectReferenceVideoMetadata(mp4, { duration: 1.8, width: 640, height: 480 }).error).toBeNull();
    expect(inspectReferenceVideoMetadata(mp4, { duration: 15.2, width: 640, height: 480 }).error).toBeNull();
    expect(inspectReferenceVideoMetadata(mp4, { duration: 1.0, width: 640, height: 480 }).error).toBe(
      copy.workbench.vgRefVideoTooShort
    );
    expect(inspectReferenceVideoMetadata(mp4, { duration: 15.3, width: 640, height: 480 }).error).toBe(
      copy.workbench.vgRefVideoTooLong
    );
  });

  it("分辨率：短边 <480 拒；480–720 通过无标记；>720 通过 + 降码告知（D9 服务端自动压缩）", () => {
    const mp4 = file("video/mp4");
    expect(inspectReferenceVideoMetadata(mp4, { duration: 5, width: 640, height: 360 }).error).toBe(
      copy.workbench.vgRefVideoResolutionLow
    );
    const ok = inspectReferenceVideoMetadata(mp4, { duration: 5, width: 1280, height: 720 });
    expect(ok.error).toBeNull();
    expect(ok.willDownscale).toBe(false);
    const hi = inspectReferenceVideoMetadata(mp4, { duration: 5, width: 1920, height: 1080 });
    expect(hi.error).toBeNull();
    expect(hi.willDownscale).toBe(true); // 短边 1080 > 720
  });

  it("格式转码标记：MOV/WEBM willTranscode=true（D9 自动转 MP4 要告知）；MP4 false", () => {
    expect(inspectReferenceVideoMetadata(file("video/quicktime"), { duration: 5, width: 640, height: 480 }).willTranscode).toBe(true);
    expect(inspectReferenceVideoMetadata(file("video/webm"), { duration: 5, width: 640, height: 480 }).willTranscode).toBe(true);
    expect(inspectReferenceVideoMetadata(file("video/mp4"), { duration: 5, width: 640, height: 480 }).willTranscode).toBe(false);
  });

  it("元数据不可读（duration 0/NaN）→ 拒", () => {
    expect(inspectReferenceVideoMetadata(file("video/mp4"), { duration: 0, width: 640, height: 480 }).error).toBe(
      copy.errors.videoUnreadable
    );
    expect(inspectReferenceVideoMetadata(file("video/mp4"), { duration: Number.NaN, width: 640, height: 480 }).error).toBe(
      copy.errors.videoUnreadable
    );
  });

  // FIX1（#216 真联调）：合计=**开区间** (1.8,15.2)（routes/videos.py:936-943 `MIN < total < MAX`）——恰 1.8/15.2 也不合法。
  it("合计三态开区间（D10 联动）：0 条=ok；≤1.8=low（含恰 1.8）；(1.8,15.2)=ok；≥15.2=over（含恰 15.2）", () => {
    expect(totalDurationStatus(0, 0)).toBe("ok"); // 无视频=可选
    expect(totalDurationStatus(1.5, 1)).toBe("low");
    expect(totalDurationStatus(1.8, 1)).toBe("low"); // 开区间：恰 1.8 不合法（BE 1800ms 不满足 MIN < total）
    expect(totalDurationStatus(1.9, 1)).toBe("ok");
    expect(totalDurationStatus(15.1, 3)).toBe("ok");
    expect(totalDurationStatus(15.2, 3)).toBe("over"); // 开区间：恰 15.2 不合法
    expect(totalDurationStatus(15.3, 2)).toBe("over");
  });
});
