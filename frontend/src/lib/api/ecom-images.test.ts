import { describe, expect, it } from "vitest";

import {
  cutoutImage,
  cutoutImageBatch,
  listModelStyles,
  listPosterTemplates,
  modelImage,
  modelImageBatch,
  posterImage,
  posterImageBatch
} from "./ecom-images";
import { getVideo, streamVideoEvents } from "./videos";

// 集成测试：不 mock hooks/handlers，真 apiFetch → MSW(vitest.setup 已 server.listen)。
// 验证 mock 忠实(吸取教训)：塞真 photo task(kind=ecom_cutout)、N clamp、SSE 不覆盖产物 URL。

describe("ecom-images API ↔ MSW（mock 忠实）", () => {
  it("单张 cutout：塞真 photo task(kind=ecom_cutout, done, 白底图 url)，GET /videos/:id 轮询拿到", async () => {
    const res = await cutoutImage({ source_asset_id: "a1", background: "white" });
    expect(res.task_id).toBeTruthy();
    const v = await getVideo(res.task_id);
    expect(v.status).toBe("done");
    expect((v as { kind?: string }).kind).toBe("ecom_cutout");
    expect(v.playback_url).toContain("cutout-white");
  });

  it("透明底 cutout：产物 url 区分(cutout-transparent)", async () => {
    const res = await cutoutImage({ source_asset_id: "a1", background: "transparent" });
    const v = await getVideo(res.task_id);
    expect(v.playback_url).toContain("cutout-transparent");
  });

  it("SSE 轮询后产物 URL 不被通用 v.mp4 覆盖(#1 回归：预 seeded 终态保留)", async () => {
    const res = await cutoutImage({ source_asset_id: "a1", background: "white" });
    // 模拟 trackExisting → subscribe：消费整段 SSE 流(running→done)
    await streamVideoEvents(res.task_id, () => undefined);
    const v = await getVideo(res.task_id);
    expect(v.playback_url).toContain("cutout-white");
    expect(v.playback_url).not.toContain("v.mp4");
  });

  it("批量 cutout：N clamp 上界 20(>20 items → 20 tasks)", async () => {
    const items = Array.from({ length: 25 }, (_, i) => ({ source_asset_id: `a${i}`, background: "white" as const }));
    const res = await cutoutImageBatch({ items });
    expect(res.batch_id).toBeTruthy();
    expect(res.tasks).toHaveLength(20);
  });

  it("批量 cutout：fan-out 各 task 可轮询到 done + cutout 产物", async () => {
    const res = await cutoutImageBatch({
      items: [
        { source_asset_id: "a1", background: "white" },
        { source_asset_id: "a2", background: "transparent" }
      ]
    });
    expect(res.tasks).toHaveLength(2);
    const v0 = await getVideo(res.tasks[0].task_id);
    expect(v0.status).toBe("done");
    expect(v0.playback_url).toContain("cutout");
  });
});

describe("ecom-images model API ↔ MSW（mock 忠实，Phase2 AI 模特）", () => {
  it("model-styles：返回非空风格预设列表(含 id/name)", async () => {
    const styles = await listModelStyles();
    expect(styles.length).toBeGreaterThan(0);
    expect(styles[0]).toHaveProperty("id");
    expect(styles[0]).toHaveProperty("name");
  });

  it("单张 model：多商品图 + 组合语义 → 塞真 photo task(kind=ecom_model, done)，GET /videos/:id 轮询拿到", async () => {
    const res = await modelImage({ product_asset_ids: ["a1", "a2"], product_images_mode: "multi_item", gender: "female", style_id: "studio" });
    expect(res.task_id).toBeTruthy();
    const v = await getVideo(res.task_id);
    expect(v.status).toBe("done");
    expect((v as { kind?: string }).kind).toBe("ecom_model");
    expect(v.playback_url).toContain("model-");
  });

  it("单张 model：模特图 + 自定义风格 → done（custom 产物 url）", async () => {
    const res = await modelImage({ product_asset_ids: ["a1"], model_asset_ids: ["m1"], product_images_mode: "multi_angle", gender: "any", custom_style: "赛博朋克霓虹" });
    await streamVideoEvents(res.task_id, () => undefined);
    const v = await getVideo(res.task_id);
    expect(v.playback_url).toContain("model-");
    expect(v.playback_url).not.toContain("v.mp4");
  });

  it("批量 model：N clamp 上界 20(>20 items → 20 tasks)", async () => {
    const items = Array.from({ length: 25 }, (_, i) => ({ source_asset_id: `a${i}`, gender: "female" as const, style_id: "studio" }));
    const res = await modelImageBatch({ items });
    expect(res.batch_id).toBeTruthy();
    expect(res.tasks).toHaveLength(20);
  });

  it("批量 model：fan-out 各 task 可轮询到 done + 模特产物", async () => {
    const res = await modelImageBatch({
      items: [
        { source_asset_id: "a1", gender: "female", style_id: "studio" },
        { source_asset_id: "a2", gender: "male", style_id: "street" }
      ]
    });
    expect(res.tasks).toHaveLength(2);
    const v0 = await getVideo(res.tasks[0].task_id);
    expect(v0.status).toBe("done");
    expect(v0.playback_url).toContain("model-");
  });
});

// ECOM-MODEL-OPTIMIZE-UI-0001 · mock 契约校验（不比 BE 宽松）：合计 ≤6 / 商品图 ≥1 / mode 合法 / 风格互斥 / extra=forbid。
type ModelBody = Parameters<typeof modelImage>[0];
describe("ecom-images model 契约校验（ECOM-MODEL-OPTIMIZE · mock 不比 BE 宽松）", () => {
  it("🔴 商品图 0 张 → 422", async () => {
    await expect(
      modelImage({ product_asset_ids: [], product_images_mode: "multi_item", gender: "female" })
    ).rejects.toMatchObject({ status: 422 });
  });

  it("🔴 商品图 + 模特图合计 > 6 → 422（商品4 + 模特3 = 7）", async () => {
    await expect(
      modelImage({ product_asset_ids: ["a1", "a2", "a3", "a4"], model_asset_ids: ["m1", "m2", "m3"], product_images_mode: "multi_item", gender: "female" })
    ).rejects.toMatchObject({ status: 422 });
  });

  it("合计正好 6（商品4 + 模特2）→ 通过", async () => {
    const res = await modelImage({ product_asset_ids: ["a1", "a2", "a3", "a4"], model_asset_ids: ["m1", "m2"], product_images_mode: "multi_item", gender: "female" });
    expect(res.task_id).toBeTruthy();
  });

  it("🔴 product_images_mode 非法 → 422", async () => {
    await expect(
      modelImage({ product_asset_ids: ["a1"], product_images_mode: "bogus", gender: "female" } as unknown as ModelBody)
    ).rejects.toMatchObject({ status: 422 });
  });

  it("🔴 style_id 与 custom_style 同时提供 → 422（互斥）", async () => {
    await expect(
      modelImage({ product_asset_ids: ["a1"], product_images_mode: "multi_item", gender: "female", style_id: "studio", custom_style: "赛博" })
    ).rejects.toMatchObject({ status: 422 });
  });

  it("🔴 多传字段（extra=forbid）→ 422", async () => {
    await expect(
      modelImage({ product_asset_ids: ["a1"], product_images_mode: "multi_item", gender: "female", bogus: 1 } as unknown as ModelBody)
    ).rejects.toMatchObject({ status: 422 });
  });

  it("风格可选：不带 style_id / custom_style 也能生成（D3）", async () => {
    const res = await modelImage({ product_asset_ids: ["a1"], product_images_mode: "multi_item", gender: "female" });
    expect(res.task_id).toBeTruthy();
  });
});

describe("ecom-images poster API ↔ MSW（mock 忠实，Phase3 营销海报）", () => {
  it("poster-templates：返回的版式 ID 逐字对齐后端真实预设(promo_bold/minimal/festival)", async () => {
    const templates = await listPosterTemplates();
    expect(templates.length).toBeGreaterThan(0);
    expect(templates[0]).toHaveProperty("id");
    expect(templates[0]).toHaveProperty("name");
    // 承重：mock 模板 id 必须与后端逐字一致，否则真后端 422（改回错 id 此断言应红）。
    const ids = templates.map((t) => t.id);
    expect(ids).toEqual(["promo_bold", "minimal", "festival"]);
  });

  it("单张 poster：塞真 photo task(kind=ecom_poster, done, 海报图 url)，GET /videos/:id 轮询拿到", async () => {
    const res = await posterImage({ source_asset_id: "a1", template_id: "promo_bold", title: "大促", subtitle: "限时" });
    expect(res.task_id).toBeTruthy();
    const v = await getVideo(res.task_id);
    expect(v.status).toBe("done");
    expect((v as { kind?: string }).kind).toBe("ecom_poster");
    expect(v.playback_url).toContain("poster-");
  });

  it("SSE 轮询后海报图 URL 不被通用 v.mp4 覆盖(预 seeded 终态保留)", async () => {
    const res = await posterImage({ source_asset_id: "a1", template_id: "festival", title: "", subtitle: "" });
    await streamVideoEvents(res.task_id, () => undefined);
    const v = await getVideo(res.task_id);
    expect(v.playback_url).toContain("poster-");
    expect(v.playback_url).not.toContain("v.mp4");
  });

  it("批量 poster：N clamp 上界 20(>20 items → 20 tasks)", async () => {
    const items = Array.from({ length: 25 }, (_, i) => ({ source_asset_id: `a${i}`, template_id: "promo_bold", title: "", subtitle: "" }));
    const res = await posterImageBatch({ items });
    expect(res.batch_id).toBeTruthy();
    expect(res.tasks).toHaveLength(20);
  });

  it("批量 poster：fan-out 各 task 可轮询到 done + 海报产物", async () => {
    const res = await posterImageBatch({
      items: [
        { source_asset_id: "a1", template_id: "promo_bold", title: "大促", subtitle: "限时" },
        { source_asset_id: "a2", template_id: "minimal", title: "", subtitle: "" }
      ]
    });
    expect(res.tasks).toHaveLength(2);
    const v0 = await getVideo(res.tasks[0].task_id);
    expect(v0.status).toBe("done");
    expect(v0.playback_url).toContain("poster-");
  });
});
