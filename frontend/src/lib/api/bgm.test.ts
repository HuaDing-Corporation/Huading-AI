import { describe, expect, it } from "vitest";

import { ApiError } from "./client";
import { listBgmLibrary } from "./bgm";
import { createVideo } from "./videos";
import type { CreateVideoRequest } from "./types";

// 集成测试：真 apiFetch → MSW。验证 mock 忠实(对齐 seam §2/§3)：配乐库 + video_gen 校验 422。

const baseReq: CreateVideoRequest = {
  topic: "赛博城市",
  prompt: "赛博城市",
  video_mode: "video_gen",
  reference_image_asset_ids: ["a1", "a2"],
  duration_sec: 10,
  resolution: "720p"
};

describe("视频生成 配乐库 + video_gen 校验 ↔ MSW（mock 忠实）", () => {
  it("配乐库：GET /bgm-library 返回免版权曲目（含 track_id/preview_url/license）", async () => {
    const tracks = await listBgmLibrary();
    expect(tracks.length).toBeGreaterThan(0);
    expect(tracks[0]).toHaveProperty("track_id");
    expect(tracks[0]).toHaveProperty("preview_url");
    expect(tracks[0]).toHaveProperty("license");
    expect(typeof tracks[0].duration_sec).toBe("number");
  });

  it("video_gen 合法（参考图1–9 + prompt + duration枚举 + resolution + 库BGM）→ 接受", async () => {
    const res = await createVideo({ ...baseReq, bgm: { source: "library", track_id: "bgm-uplift" } });
    expect(res.id).toBeTruthy();
  });

  it("video_gen 非法 → 422（空参考图 / 空prompt / 超9 / 非法duration / 非法resolution / 非法库track）", async () => {
    const bads: CreateVideoRequest[] = [
      { ...baseReq, reference_image_asset_ids: [] },
      { ...baseReq, prompt: "" },
      { ...baseReq, reference_image_asset_ids: Array.from({ length: 10 }, (_, i) => `a${i}`) },
      { ...baseReq, duration_sec: 7 },
      { ...baseReq, resolution: "2160p" },
      { ...baseReq, bgm: { source: "library", track_id: "nope" } }
    ];
    for (const bad of bads) {
      let caught: unknown;
      try {
        await createVideo(bad);
      } catch (e) {
        caught = e;
      }
      expect((caught as ApiError)?.status).toBe(422);
    }
  });

  // FIX1 承重：参考图重复 → 422（对齐后端 schemas/videos.py:221 唯一性；mock 不得放宽）。
  it("video_gen 参考图重复 → 422（唯一性，对齐后端）", async () => {
    let caught: unknown;
    try {
      await createVideo({ ...baseReq, reference_image_asset_ids: ["dup", "dup"] });
    } catch (e) {
      caught = e;
    }
    expect((caught as ApiError)?.status).toBe(422);
  });

  it("video_gen 上传BGM（asset_id）合法 → 接受", async () => {
    const res = await createVideo({ ...baseReq, bgm: { source: "upload", asset_id: "audio-1" } });
    expect(res.id).toBeTruthy();
  });
});
