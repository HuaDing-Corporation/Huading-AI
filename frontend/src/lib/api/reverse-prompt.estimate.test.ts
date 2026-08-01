import { describe, expect, it } from "vitest";

import { estimateReversePrompt } from "@/lib/api/reverse-prompt";

/**
 * REVERSE-DEEP-UI-0001-FIX1 · 承重门9 的**契约一半**：`POST /reverse-prompt/estimate` 三档金额。
 * （另一半「弹窗显示的就是这个数」在 reverse-prompt-form.video.test.tsx。两半都要，因为：
 *   只测契约 → 弹窗写死 150 也绿；只测弹窗 → 桩给什么就是什么，档位算错也绿。）
 *
 * 真走 MSW（非 stub）：apiFetch → handlers.ts 的 estimate handler，验响应形状 + 三档取值 + 校验严格度。
 *
 * ✅ FIX2 真联调**已核**（BE #219 已合入 develop）：三档取值取自 TestClient 真实响应体 ——
 *    图片 `{credits:30, duration_sec:null, tier:"image"}`、60_000ms `{150, 60.0, "video_short"}`、
 *    60_001ms `{250, 60.001, "video_long"}`（BE 自测同款：tests/test_reverse_prompt_pipeline.py:1311-1323）。
 *
 * 🔴 **image 档的 30 绝不许写进业务逻辑的断言里**：它来自 CreditRate 表（迁移播种的
 *    `reverse-prompt-call-rate`），**每个租户都能被改写**。本文件只在「mock 与 BE 同源」这一层断它，
 *    展示层一律照抄后端返回值 —— 计费门那边断的是「显示的就是响应里的那个数」，而不是「显示 30」。
 */
describe("estimateReversePrompt · POST /reverse-prompt/estimate（§八 M4，真走 MSW）", () => {
  it("🔴 承重门9 · 短视频档（≤60s）→ 150 积分 / tier=video_short", async () => {
    const res = await estimateReversePrompt({ source_asset_id: "video-asset-1" }); // mock 约定 3s
    expect(res).toEqual({ credits: 150, duration_sec: 3, tier: "video_short" });
  });

  it("🔴 承重门9 · 长视频档（61–180s）→ 250 积分 / tier=video_long（**不是** 150）", async () => {
    const res = await estimateReversePrompt({ source_asset_id: "video-asset-long-1" }); // mock 约定 180s
    expect(res).toEqual({ credits: 250, duration_sec: 180, tier: "video_long" });
    // 这一句是本包存在的理由：分档后同一个端点对不同素材必须给出不同金额。
    expect(res.credits).not.toBe(150);
  });

  it("图片档 → tier=image、duration_sec 为 null（图片没有时长可言，不冒充 0）", async () => {
    const res = await estimateReversePrompt({ source_asset_id: "upload-1" });
    expect(res.tier).toBe("image");
    expect(res.duration_sec).toBeNull();
    // 🔴 只断类型不断数值：image 档费率是租户可覆写的 CreditRate，不是常量（见文件抬头）。
    expect(typeof res.credits).toBe("number");
    expect(res.credits).toBeGreaterThan(0);
  });

  // ── 🔴 承重门16「mock 不比 BE 宽松」：存在性由**唯一资产注册表**说了算 ─────────────────
  // BE 的 estimate 第一步是 `source_asset_or_raise`：资产不存在/跨租户 → **404
  // REVERSE_PROMPT_SOURCE_NOT_FOUND**（TestClient 实测响应体；BE 自测 tests:1326-1327 亦断言）。
  //
  // 🔴 FIX3（CB P1-1）：下面第二条才是真正的承重。上一版只有「非法形状」那条 ——
  //    它锁住的是**格式校验**，而当时 mock 判存在性用的正是正则，于是这条测试与被测实现是同义反复：
  //    `upload-999`（合法形状、从未上传）照样 200/30，真 BE 会 404，**测试对此完全无感**。
  //    CB 实测抓到了这一点。现在 mock 改查注册表，这两条才分别锁住「格式」与「真实存在性」。
  // 变异：把 handlers.ts estimate handler 里的 `getMockAssetForTenant(...)` 换回正则判存在性 →
  //       下面两条（合法形状不存在 / 跨租户）必红。
  it("承重门16 · 非法形状的 id → 404（格式面）", async () => {
    await expect(estimateReversePrompt({ source_asset_id: "no-such-asset" })).rejects.toMatchObject({
      status: 404,
      code: "REVERSE_PROMPT_SOURCE_NOT_FOUND"
    });
  });

  it("🔴 承重门16 · **合法形状但从未上传**的资产 → 404（真实存在性，正则判据下会假绿 200）", async () => {
    // `upload-999` 完全符合 mock 的 id 形状（uploads handler 就是发 `upload-N`），
    // 只是**注册表里没有** —— 真 BE 对它 404。这条是 CB P1-1 指出的那个缺口。
    await expect(estimateReversePrompt({ source_asset_id: "upload-999" })).rejects.toMatchObject({
      status: 404,
      code: "REVERSE_PROMPT_SOURCE_NOT_FOUND"
    });
    await expect(estimateReversePrompt({ source_asset_id: "video-asset-999" })).rejects.toMatchObject({
      status: 404
    });
  });

  it("🔴 承重门16 · **跨租户**资产 → 404（存在但不属于我，BE 不泄露存在性、同码同状态）", async () => {
    // `upload-other-tenant-1` 在注册表里**真实存在**（所以走不到「查不到」那条路径），
    // 但 tenant_id 是别人的 → 必须与「不存在」给出完全相同的 404 + 同一个 error_code。
    await expect(estimateReversePrompt({ source_asset_id: "upload-other-tenant-1" })).rejects.toMatchObject({
      status: 404,
      code: "REVERSE_PROMPT_SOURCE_NOT_FOUND"
    });
  });

  it("对照组：注册表里属于本租户的资产 → 200（防「一刀切全 404」式的假通过）", async () => {
    // 🔴 前三条都是「该 404」。没有这条对照，把 handler 改成无条件 404 也能让上面全绿 ——
    //    那是比 BE **更严**（误杀合法请求），与「不比 BE 宽松」同等违规。
    const res = await estimateReversePrompt({ source_asset_id: "upload-1" });
    expect(res.tier).toBe("image");
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
