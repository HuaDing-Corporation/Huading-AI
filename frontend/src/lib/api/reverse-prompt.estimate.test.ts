import { describe, expect, it } from "vitest";

import { estimateReversePrompt } from "@/lib/api/reverse-prompt";

/**
 * REVERSE-DEEP-UI-0001-FIX1 · 承重门9 的**契约一半**：`POST /reverse-prompt/estimate` 三档金额。
 * （另一半「弹窗显示的就是这个数」在 reverse-prompt-form.video.test.tsx。两半都要，因为：
 *   只测契约 → 弹窗写死 100 也绿；只测弹窗 → 桩给什么就是什么，档位算错也绿。）
 *
 * 真走 MSW（非 stub）：apiFetch → handlers.ts 的 estimate handler，验响应形状 + 三档取值 + 校验严格度。
 * 🔴 三档金额与 BE 同源坐标见 handlers.ts 的 REVERSE_ESTIMATE_* 常量注释
 *    （image=quota.py:465 默认费率 / video_short=config.py:205=100 / video_long=§八 D9 新增 250）。
 * ⚠️ BE 包尚未合入 develop，本轮按 §八 v2 契约 mock 先行；BE 合并后逐字复核这三个数与 tier 字面量。
 */
describe("estimateReversePrompt · POST /reverse-prompt/estimate（§八 M4，真走 MSW）", () => {
  it("🔴 承重门9 · 短视频档（≤60s）→ 100 积分 / tier=video_short", async () => {
    const res = await estimateReversePrompt({ source_asset_id: "video-asset-1" }); // mock 约定 3s
    expect(res).toEqual({ credits: 100, duration_sec: 3, tier: "video_short" });
  });

  it("🔴 承重门9 · 长视频档（61–180s）→ 250 积分 / tier=video_long（**不是** 100）", async () => {
    const res = await estimateReversePrompt({ source_asset_id: "video-asset-long-1" }); // mock 约定 180s
    expect(res).toEqual({ credits: 250, duration_sec: 180, tier: "video_long" });
    // 这一句是本包存在的理由：分档后同一个端点对不同素材必须给出不同金额。
    expect(res.credits).not.toBe(100);
  });

  it("图片档 → tier=image、duration_sec 为 null（图片没有时长可言，不冒充 0）", async () => {
    const res = await estimateReversePrompt({ source_asset_id: "upload-1" });
    expect(res.tier).toBe("image");
    expect(res.duration_sec).toBeNull();
    expect(typeof res.credits).toBe("number");
  });

  it("🔴 档位不由客户端决定：请求体多带 duration_sec / tier → 422（BE extra=forbid）", async () => {
    // D9 原话「档位由上传时已落库的 duration_ms 判定，不由客户端传参决定」——这道 422 就是它的执行面。
    // mock 若在这里放行，前端就能自己报价，而 mock 全绿、生产 422。
    await expect(
      estimateReversePrompt({ source_asset_id: "video-asset-1", duration_sec: 3 } as never)
    ).rejects.toMatchObject({ status: 422 });
    await expect(
      estimateReversePrompt({ source_asset_id: "video-asset-1", tier: "video_short" } as never)
    ).rejects.toMatchObject({ status: 422 });
  });

  it("缺 source_asset_id → 422（与 POST /reverse-prompt 同口径）", async () => {
    await expect(estimateReversePrompt({} as never)).rejects.toMatchObject({ status: 422 });
  });
});
