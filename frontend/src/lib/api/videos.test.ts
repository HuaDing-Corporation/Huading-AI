import { describe, expect, it } from "vitest";

import { createVideo, estimateVideo, generateScenePrompt } from "@/lib/api/videos";

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

  // FIX1（CB P1 · 提交/预估路径）：VideoGenerateRequest.duration_sec 也是 int，小数 → 422。
  it("防假绿：estimate 小数 duration_sec(5.5) → 422", async () => {
    await expect(
      estimateVideo({ video_mode: "seedance_i2v", product_image_keys: ["uploads/x.png"], voice_id: "v1", duration_sec: 5.5 })
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

  // FIX1（CB P1）防假绿：真 BE duration_sec 是 int，小数/字符串 → 422（不四舍五入放行；此前 round 5.5→6 是假绿，线上真 422）。
  it("小数 duration_sec(5.5/5.4) → 422（镜像 BE int，不放行）", async () => {
    await expect(generateScenePrompt({ product_image_keys: ["uploads/x.png"], duration_sec: 5.5 })).rejects.toThrow();
    await expect(generateScenePrompt({ product_image_keys: ["uploads/x.png"], duration_sec: 5.4 })).rejects.toThrow();
  });

  it("字符串 duration_sec('5.5') → 422（BE int 也拒字符串）", async () => {
    await expect(
      // @ts-expect-error 故意传非法类型：真 BE int 拒字符串，mock 须同样 422（防假绿）
      generateScenePrompt({ product_image_keys: ["uploads/x.png"], duration_sec: "5.5" })
    ).rejects.toThrow();
  });
});

// IMAGE-GEN-OPTIMIZE-UI-0001 §四：photo 提交 mock 校验（createVideo 真走 MSW）。mock 不比 BE 宽松：image_keys 1–6、
// 四强度 10..100 步10；未开启不出现。⚠️ 零回归：AI 封面(purpose:cover + image_size/image_quality，无 image_keys/强度)天然全过。
// FIX1 真联调：image_keys 须为 BE 格式 uploads/<name>.{jpg,jpeg,png,webp}（POST /uploads 返回值），mock 已对齐收紧。
const refKey = (i: number) => `uploads/ref-${i}.png`;
const sixRefs = Array.from({ length: 6 }, (_, i) => refKey(i));

describe("createVideo · photo 提交校验（apiFetch 真走 MSW · IMAGE-GEN-OPTIMIZE-UI-0001）", () => {
  it("合法 photo（6 张参考图 + 相似度 80）→ 202 accepted", async () => {
    const res = await createVideo({
      topic: "一只橘猫",
      video_mode: "photo",
      image_keys: sixRefs,
      similarity_strength: 80,
      aspect_ratio: "1:1"
    });
    expect(res.status).toBe("queued");
  });

  it("防假绿：image_keys 7 张 → 422（1–6 上限）", async () => {
    await expect(
      createVideo({ topic: "x", video_mode: "photo", image_keys: [...sixRefs, refKey(6)] })
    ).rejects.toThrow();
  });

  // FIX1 真联调：逐字对齐 BE _IMAGE_KEY_RE——非 uploads/*.{jpg,jpeg,png,webp} 的假 key → 422（mock 此前放行「a」是假绿）。
  it("防假绿：image_keys 格式非法（裸「a」非 uploads/*）→ 422", async () => {
    await expect(createVideo({ topic: "x", video_mode: "photo", image_keys: ["a"] })).rejects.toThrow();
    await expect(
      createVideo({ topic: "x", video_mode: "photo", image_keys: ["uploads/x.gif"] })
    ).rejects.toThrow(); // gif 不在 BE 白名单
  });

  // FIX1 真联调：四层提示词各 ≤20000（BE Field max_length + extra=forbid）。20001 → 422；20000 → 放行。
  it("防假绿：提示词超 20000 → 422；恰 20000 → 202", async () => {
    await expect(
      createVideo({ topic: "x".repeat(20001), video_mode: "photo" })
    ).rejects.toThrow();
    const ok = await createVideo({ topic: "x".repeat(20000), video_mode: "photo", master_prompt: "y".repeat(20000) });
    expect(ok.status).toBe("queued");
  });

  it("防假绿：强度非步长10（55）→ 422", async () => {
    await expect(createVideo({ topic: "x", video_mode: "photo", subject_strength: 55 })).rejects.toThrow();
  });

  it("防假绿：强度越界（110）→ 422", async () => {
    await expect(createVideo({ topic: "x", video_mode: "photo", creativity_strength: 110 })).rejects.toThrow();
  });

  it("§3之二：合法 image_resolution(2k) → 202；非法(8k) → 422（mock 不比 BE 宽松）", async () => {
    const ok = await createVideo({ topic: "x", video_mode: "photo", image_resolution: "2k" });
    expect(ok.status).toBe("queued");
    await expect(createVideo({ topic: "x", video_mode: "photo", image_resolution: "8k" })).rejects.toThrow();
  });

  it("零回归：AI 封面（purpose:cover + image_size/image_quality，无 image_keys/强度）→ 202 全过", async () => {
    const res = await createVideo({
      topic: "封面",
      video_mode: "photo",
      purpose: "cover",
      image_size: "1024x1536",
      image_quality: "medium"
    });
    expect(res.status).toBe("queued");
  });
});
