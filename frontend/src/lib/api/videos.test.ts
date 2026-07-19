import { describe, expect, it } from "vitest";

import { estimateVideo, generateScenePrompt } from "@/lib/api/videos";

// ECOM-VIDEO-OPTIMIZE-UI-0001 · FIX2 · P1：确认窗打开必调 POST /videos/estimate。此前缺 mock handler →
// MSW 放行到真后端 → CI net::ERR_FAILED（#203 红）。本测真走 apiFetch → 全局 MSW（vitest.setup 已 server.listen），
// 非 stub：验响应契约形状（对齐 #202 VideoEstimateResponse）+ 防假绿（estimate 与提交同一 VideoGenerateRequest 校验，
// 缺产品图/音色在 estimate 阶段即 422，mock 不比 BE 宽松）。
describe("estimateVideo · POST /videos/estimate（apiFetch 真走 MSW · FIX2 P1）", () => {
  it("seedance_i2v 合法体 → 返回契约形状 {estimated_credits:number, unit:'credits', note}", async () => {
    const res = await estimateVideo({
      video_mode: "seedance_i2v",
      product_image_keys: ["uploads/mock-product-1.png"],
      voice_id: "v1",
      duration_sec: 30,
      resolution: "720p"
    });
    expect(res.unit).toBe("credits");
    expect(typeof res.estimated_credits).toBe("number");
    expect(res.estimated_credits).toBeGreaterThan(0);
    expect(res.note).toBeTruthy(); // #202 _ESTIMATE_NOTE
  });

  it("防假绿：seedance_i2v 缺产品图 → estimate 阶段即 422（不放到提交才红）", async () => {
    await expect(
      estimateVideo({ video_mode: "seedance_i2v", voice_id: "v1", duration_sec: 30 })
    ).rejects.toThrow();
  });

  it("防假绿：seedance_i2v 缺音色 → 422", async () => {
    await expect(
      estimateVideo({ video_mode: "seedance_i2v", product_image_keys: ["uploads/x.png"], duration_sec: 30 })
    ).rejects.toThrow();
  });
});

// ECOM-VIDEO-SCENE-DURATION-FIX-UI-0001：scene-prompt 补传 duration_sec（Cowork 冻结 §4.2 漏了它，致秒数恒「约15秒」）。
// 真走 apiFetch→MSW，验 ①duration 透传后 mock scene_prompt 反映该时长；②mock 夹取 [5,120] 镜像 BE _clamp_duration（不 reject）。
describe("generateScenePrompt · duration 透传（apiFetch 真走 MSW · SCENE-DURATION-FIX）", () => {
  it("带 duration_sec:10 → scene_prompt 反映「约 10 秒」（秒数随选择变化，非恒 15）", async () => {
    const res = await generateScenePrompt({ product_image_keys: ["uploads/mock-product-1.png"], duration_sec: 10 });
    expect(res.scene_prompt).toContain("约 10 秒");
  });

  it("BE 夹取 [5,120] 镜像：传 3 → 夹到 5；传 200 → 夹到 120（不 reject，mock 不比 BE 宽松）", async () => {
    expect(
      (await generateScenePrompt({ product_image_keys: ["uploads/x.png"], duration_sec: 3 })).scene_prompt
    ).toContain("约 5 秒");
    expect(
      (await generateScenePrompt({ product_image_keys: ["uploads/x.png"], duration_sec: 200 })).scene_prompt
    ).toContain("约 120 秒");
  });
});
