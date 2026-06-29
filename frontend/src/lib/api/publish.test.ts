import { describe, expect, it } from "vitest";

import { ApiError } from "./client";
import { createPublishDrafts, deletePublishRecord, listPublishPlatforms, listPublishRecords, markPublished } from "./publish";

// 集成测试：真 apiFetch → MSW。验证 mock 忠实(逐字对齐 backend/app/schemas/publish.py)：
// platforms(含 title_max)、drafts 返 {id,items}(PublishDraftItem)、records 嵌套、PATCH 标记单平台、DELETE。

describe("publish API ↔ MSW（mock 忠实，对齐后端 schema）", () => {
  it("platforms：5 平台(wxchannels 在)，含 title_max/publish_url 等字段", async () => {
    const ps = await listPublishPlatforms();
    expect(ps.length).toBe(5);
    expect(ps.map((p) => p.id)).toContain("wxchannels");
    expect(ps.map((p) => p.id)).not.toContain("wechat_channels");
    expect(typeof ps[0].title_max).toBe("number");
  });

  it("drafts：返回 {id, items:[PublishDraftItem]}，items 字段对齐(platform_id/body/hashtags/media_url)", async () => {
    const res = await createPublishDrafts({ source_kind: "video", source_task_id: "v1", platforms: ["douyin", "bilibili"] });
    expect(res.id).toBeTruthy();
    expect(res.items).toHaveLength(2);
    const it0 = res.items[0];
    expect(it0).toHaveProperty("platform_id");
    expect(it0).toHaveProperty("body");
    expect(it0).toHaveProperty("hashtags");
    expect(it0).toHaveProperty("media_url");
    expect(typeof it0.publish_url).toBe("string");
  });

  it("drafts 422：source_kind 非法 / 空产物 / 非法平台", async () => {
    for (const bad of [
      { source_kind: "photo", source_task_id: "v1", platforms: ["douyin"] },
      { source_kind: "video", source_task_id: "", platforms: ["douyin"] },
      { source_kind: "video", source_task_id: "v1", platforms: ["bogus"] }
    ]) {
      let caught: unknown;
      try {
        await createPublishDrafts(bad as never);
      } catch (e) {
        caught = e;
      }
      expect((caught as ApiError)?.status).toBe(422);
    }
  });

  it("records：drafts 后记录为嵌套(id + source + platforms[]{platform_id,status:draft})", async () => {
    const res = await createPublishDrafts({ source_kind: "image", source_task_id: "img1", platforms: ["xiaohongshu"] });
    const records = await listPublishRecords();
    const rec = records.find((r) => r.id === res.id);
    expect(rec).toBeTruthy();
    expect(rec?.source_kind).toBe("image");
    expect(rec?.platforms[0]).toMatchObject({ platform_id: "xiaohongshu", status: "draft" });
  });

  it("PATCH：标记记录下某平台 published(其余不变)", async () => {
    const res = await createPublishDrafts({ source_kind: "video", source_task_id: "v3", platforms: ["douyin", "kuaishou"] });
    const updated = await markPublished(res.id, "douyin");
    const douyin = updated.platforms.find((p) => p.platform_id === "douyin");
    const kuaishou = updated.platforms.find((p) => p.platform_id === "kuaishou");
    expect(douyin?.status).toBe("published");
    expect(kuaishou?.status).toBe("draft");
  });

  it("DELETE：返 {id, deleted_at}，移除后列表不含；未知 id → 404", async () => {
    const res = await createPublishDrafts({ source_kind: "video", source_task_id: "v4", platforms: ["bilibili"] });
    const del = await deletePublishRecord(res.id);
    expect(del.deleted_at).toBeTruthy();
    const records = await listPublishRecords();
    expect(records.find((r) => r.id === res.id)).toBeUndefined();

    let caught: unknown;
    try {
      await deletePublishRecord("nope");
    } catch (e) {
      caught = e;
    }
    expect((caught as ApiError).status).toBe(404);
  });
});
