import { describe, expect, it } from "vitest";
import { createVideo, getVideo, listVideosPage } from "@/lib/api/videos";

// Exercise real FE transport and MSW. Snapshot is read-only, never in POST input.
describe("HeyGen mock list/detail snapshots", () => {
  it.each([
    [{ avatar_asset_id: "photo-fixture" }, "avatar_iv"],
    [{ avatar_video_asset_id: "video-fixture" }, "lipsync_precision"]
  ] as const)("%j preserves voice and reads %s from both response surfaces", async (source, model) => {
    const result = await createVideo({ topic: "mock口播", script: "测试文案", voice_id: "v-zhixing", ...source });
    const detail = await getVideo(result.id);
    const list = await listVideosPage({ mode: "avatar_talk" });
    expect(detail.avatar_provider).toBe("heygen");
    expect(detail.avatar_model).toBe(model);
    expect(detail.voice_id).toBe("v-zhixing");
    expect(list.items.find((item) => item.id === result.id)).toMatchObject({ avatar_provider: "heygen", avatar_model: model });
  });
});
