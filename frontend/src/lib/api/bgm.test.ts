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

  it("video_gen 合法（参考图1–9 + prompt + duration 4–15 + resolution + 库BGM）→ 接受", async () => {
    const res = await createVideo({ ...baseReq, bgm: { source: "library", track_id: "bgm-uplift" } });
    expect(res.id).toBeTruthy();
  });

  it("video_gen 非法 → 422（空prompt / 超9 / 非法duration / 非法resolution / 非法库track）", async () => {
    // FIX1 真联调（#213 合并源 schemas/videos.py:355）：参考图 **0–9**——空参考图=纯文生视频合法，已从非法组移出（见下条）。
    const bads: CreateVideoRequest[] = [
      { ...baseReq, prompt: "" },
      { ...baseReq, reference_image_asset_ids: Array.from({ length: 10 }, (_, i) => `a${i}`) },
      { ...baseReq, duration_sec: 20 }, // VIDEO-GEN-PARAMS-UI-0001：时长改整数 4–15（7 现已合法）；20 越界仍 422
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

  // FIX1 真联调：BE 参考图 0–9（0 张=纯文生视频合法，schemas/videos.py:355）——mock 同步放开下限，不再比 BE 严。
  // V2V-UI-0001 起 UI 参考图也已放开可选（「参考图或视频（可选）」），mock 层忠实 BE。
  it("video_gen 参考图 0 张（纯文生视频）→ 接受（mock 对齐 BE 0–9，不比 BE 严）", async () => {
    const res = await createVideo({ ...baseReq, reference_image_asset_ids: [] });
    expect(res.id).toBeTruthy();
  });

  // V2V-UI-0001（D8/D10 · mock 先行）：图+视频同传 → 422 互斥；视频 ≤3 且唯一；合法视频列表 → 接受。
  it("V2V：图+视频同传 → 422（D8 严格二选一，provider 不能同用）", async () => {
    let caught: unknown;
    try {
      await createVideo({ ...baseReq, reference_video_asset_ids: ["v1"] }); // baseReq 自带参考图
    } catch (e) {
      caught = e;
    }
    expect((caught as ApiError)?.status).toBe(422);
  });

  it("V2V：仅视频 ≤3 条 → 接受；4 条 → 422；重复 → 422", async () => {
    const noRefs = { ...baseReq, reference_image_asset_ids: [] };
    const ok = await createVideo({ ...noRefs, reference_video_asset_ids: ["v1", "v2", "v3"] });
    expect(ok.id).toBeTruthy();
    for (const bad of [
      { ...noRefs, reference_video_asset_ids: ["v1", "v2", "v3", "v4"] },
      { ...noRefs, reference_video_asset_ids: ["dup", "dup"] }
    ]) {
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
