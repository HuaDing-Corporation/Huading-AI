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

  // FIX1 真联调：四层提示词各 ≤20000（BE schema 校验）。20001 → 422；20000 → 放行。
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

// VIDEO-GEN-PARAMS-UI-0001 §6：video_gen 提交 mock 校验（createVideo 真走 MSW）。mock 不比 BE 宽松：
// 时长整数 4–15（3/16/20/5.5→422）、提示词 topic≤2000（2001→422）、画面比例 7 值（非法→422）、
// generate_audio/negative_prompt 随请求传（extra=forbid 不误杀）。⚠️ 参考图 1–9 唯一、prompt 非空为既有门。
describe("createVideo · video_gen 提交校验（apiFetch 真走 MSW · VIDEO-GEN-PARAMS-UI-0001）", () => {
  const base = {
    video_mode: "video_gen" as const,
    prompt: "赛博夜景",
    topic: "赛博夜景",
    reference_image_asset_ids: ["a1"]
  };

  it("合法 video_gen（时长8 + 16:9 + 音频开 + 负面）→ 202 queued", async () => {
    const res = await createVideo({
      ...base,
      duration_sec: 8,
      aspect_ratio: "16:9",
      generate_audio: true,
      negative_prompt: "水印、变形"
    });
    expect(res.status).toBe("queued");
  });

  it("防假绿：时长越界/小数（3/16/20/5.5）→ 422（整数 4–15）", async () => {
    for (const d of [3, 16, 20, 5.5]) {
      await expect(createVideo({ ...base, duration_sec: d })).rejects.toThrow();
    }
  });

  it("防假绿：预设边界 4 与 15 合法 → 202；且旧的仅 5/10/15 已放宽", async () => {
    expect((await createVideo({ ...base, duration_sec: 4 })).status).toBe("queued");
    expect((await createVideo({ ...base, duration_sec: 15 })).status).toBe("queued");
  });

  it("防假绿：提示词 topic 超 2000（2001）→ 422；恰 2000 → 202", async () => {
    const s = (n: number) => "文".repeat(n);
    await expect(createVideo({ ...base, prompt: s(2001), topic: s(2001), duration_sec: 8 })).rejects.toThrow();
    expect((await createVideo({ ...base, prompt: s(2000), topic: s(2000), duration_sec: 8 })).status).toBe("queued");
  });

  it("防假绿：画面比例非法（8:1）→ 422；7 值任一（3:4）→ 202", async () => {
    await expect(createVideo({ ...base, duration_sec: 8, aspect_ratio: "8:1" })).rejects.toThrow();
    expect((await createVideo({ ...base, duration_sec: 8, aspect_ratio: "3:4" })).status).toBe("queued");
  });
});

// Code Review（VIDEO-GEN-PARAMS-UI-0001）：estimate 与提交同门——video_gen 非法时长/比例在 estimate 阶段即 422
// （此前 estimate 缺该分支 → estimate 200 而提交 422，estimate 比提交宽松=假绿口）。
describe("estimateVideo · video_gen 同门校验（Code Review 补）", () => {
  it("estimate video_gen 时长 20（越界）→ 422（不放到提交才红）", async () => {
    await expect(
      estimateVideo({ video_mode: "video_gen", duration_sec: 20, resolution: "720p" })
    ).rejects.toThrow();
  });

  it("estimate video_gen 非法比例（2:3）→ 422；合法（8s + adaptive）→ 200", async () => {
    await expect(
      estimateVideo({ video_mode: "video_gen", duration_sec: 8, aspect_ratio: "2:3" })
    ).rejects.toThrow();
    const ok = await estimateVideo({ video_mode: "video_gen", duration_sec: 8, aspect_ratio: "auto" });
    expect(ok.estimated_credits).toBeGreaterThan(0);
  });
});

// Code Review：2000 墙按**码点**计数对齐 BE Python len()——1001 个增补面 emoji（UTF-16 长 2002）按码点是 1001 ≤ 2000，
// 不得误杀；1001+1000 个普通字仍拦。
describe("createVideo · 2000 墙码点计数（Code Review 补）", () => {
  const vg = { video_mode: "video_gen" as const, reference_image_asset_ids: ["a1"], duration_sec: 8 };
  it("1001 个 emoji（UTF-16 2002 码元 / 1001 码点）→ 202 不误杀", async () => {
    const emoji = "😀".repeat(1001);
    const res = await createVideo({ ...vg, prompt: emoji, topic: emoji });
    expect(res.status).toBe("queued");
  });
  it("2001 码点（普通字符）→ 仍 422", async () => {
    const s = "文".repeat(2001);
    await expect(createVideo({ ...vg, prompt: s, topic: s })).rejects.toThrow();
  });
});
