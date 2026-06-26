import { describe, expect, it } from "vitest";

import { cutoutImage, cutoutImageBatch } from "./ecom-images";
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
