import { describe, expect, it } from "vitest";

import { cutoutImage, cutoutImageBatch, listModelStyles, modelImage, modelImageBatch } from "./ecom-images";
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

  it("单张 model：塞真 photo task(kind=ecom_model, done, 模特图 url)，GET /videos/:id 轮询拿到", async () => {
    const res = await modelImage({ source_asset_id: "a1", gender: "female", style_id: "studio" });
    expect(res.task_id).toBeTruthy();
    const v = await getVideo(res.task_id);
    expect(v.status).toBe("done");
    expect((v as { kind?: string }).kind).toBe("ecom_model");
    expect(v.playback_url).toContain("model-");
  });

  it("SSE 轮询后模特图 URL 不被通用 v.mp4 覆盖(预 seeded 终态保留)", async () => {
    const res = await modelImage({ source_asset_id: "a1", gender: "any", style_id: "street" });
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
