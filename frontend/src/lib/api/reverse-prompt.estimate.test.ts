import { describe, expect, it } from "vitest";

import { estimateReversePrompt } from "@/lib/api/reverse-prompt";

/**
 * REVERSE-DEEP-UI-0001-FIX1 · 承重门9 的**契约一半**：`POST /reverse-prompt/estimate` 三档金额。
 * （另一半「弹窗显示的就是这个数」在 reverse-prompt-form.video.test.tsx。两半都要，因为：
 *   只测契约 → 弹窗写死 100 也绿；只测弹窗 → 桩给什么就是什么，档位算错也绿。）
 *
 * 真走 MSW（非 stub）：apiFetch → handlers.ts 的 estimate handler，验响应形状 + 三档取值 + 校验严格度。
 *
 * ✅ FIX2 真联调**已核**（BE #219 已合入 develop）：三档取值取自 TestClient 真实响应体 ——
 *    图片 `{credits:30, duration_sec:null, tier:"image"}`、60_000ms `{100, 60.0, "video_short"}`、
 *    60_001ms `{250, 60.001, "video_long"}`（BE 自测同款：tests/test_reverse_prompt_pipeline.py:1311-1323）。
 *
 * 🔴 **image 档的 30 绝不许写进业务逻辑的断言里**：它来自 CreditRate 表（迁移播种的
 *    `reverse-prompt-call-rate`），**每个租户都能被改写**。本文件只在「mock 与 BE 同源」这一层断它，
 *    展示层一律照抄后端返回值 —— 计费门那边断的是「显示的就是响应里的那个数」，而不是「显示 30」。
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
    // 🔴 只断类型不断数值：image 档费率是租户可覆写的 CreditRate，不是常量（见文件抬头）。
    expect(typeof res.credits).toBe("number");
    expect(res.credits).toBeGreaterThan(0);
  });

  // ── 🔴 FIX2 真联调新增：承重门16「mock 不比 BE 宽松」───────────────────────────────
  // BE 的 estimate 第一步是 `source_asset_or_raise`：资产不存在/跨租户 → **404
  // REVERSE_PROMPT_SOURCE_NOT_FOUND**（TestClient 实测响应体；BE 自测 tests:1326-1327 亦断言）。
  // 上一版 mock 对**任意非空字符串**恒 200 报价 —— 本地拿到金额、生产拿到 404 的典型假绿。
  // 变异：把 handlers.ts estimate handler 里的 `mockAssetExists` 判据删掉 → 本条必红。
  it("🔴 承重门16 · 资产不存在 → 404 REVERSE_PROMPT_SOURCE_NOT_FOUND（不是 200 报个价）", async () => {
    await expect(estimateReversePrompt({ source_asset_id: "no-such-asset" })).rejects.toMatchObject({
      status: 404,
      code: "REVERSE_PROMPT_SOURCE_NOT_FOUND"
    });
  });

  it("🔴 承重门16 · source_asset_id 超长（>36）→ 422（BE Field(max_length=36)，不是静默截断）", async () => {
    await expect(estimateReversePrompt({ source_asset_id: "u".repeat(37) })).rejects.toMatchObject({ status: 422 });
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
