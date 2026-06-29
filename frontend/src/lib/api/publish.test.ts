import { describe, expect, it } from "vitest";

import { ApiError } from "./client";
import { createPublishDrafts, deletePublishRecord, listPublishPlatforms, listPublishRecords, markPublished } from "./publish";

// 集成测试：真 apiFetch → MSW。验证 mock 忠实(§1-5)：platforms 5、drafts 按平台生成、records 列表、
// PATCH 标记已发布、DELETE 移除、status 枚举 draft/published。吸取声音克隆/0006 mock 掩盖契约教训。

describe("publish API ↔ MSW（mock 忠实，§1-5）", () => {
  it("platforms：返回 5 平台(含 id/name)", async () => {
    const ps = await listPublishPlatforms();
    expect(ps.length).toBe(5);
    expect(ps.map((p) => p.id)).toContain("douyin");
    expect(ps[0]).toHaveProperty("name");
  });

  it("drafts：按所选平台各生成一条 draft 记录(status=draft + publish_url)", async () => {
    const drafts = await createPublishDrafts({ source_kind: "video", source_task_id: "v1", platforms: ["douyin", "bilibili"] });
    expect(drafts).toHaveLength(2);
    expect(drafts.every((d) => d.status === "draft")).toBe(true);
    expect(drafts.every((d) => typeof d.publish_url === "string" && d.publish_url.length > 0)).toBe(true);
    expect(drafts.map((d) => d.platform).sort()).toEqual(["bilibili", "douyin"]);
  });

  it("drafts：缺产物或空平台 → 422", async () => {
    let caught: unknown;
    try {
      await createPublishDrafts({ source_kind: "video", source_task_id: "", platforms: [] });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(422);
  });

  it("drafts：含非法平台 id → 422(严格枚举，不静默丢弃，对齐后端)", async () => {
    let caught: unknown;
    try {
      await createPublishDrafts({ source_kind: "video", source_task_id: "v1", platforms: ["douyin", "bogus"] as never });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(422);
  });

  it("records：drafts 进入记录列表", async () => {
    const drafts = await createPublishDrafts({ source_kind: "video", source_task_id: "v2", platforms: ["kuaishou"] });
    const records = await listPublishRecords();
    expect(records.find((r) => r.id === drafts[0].id)).toBeTruthy();
  });

  it("PATCH：标记已发布 → status=published", async () => {
    const drafts = await createPublishDrafts({ source_kind: "video", source_task_id: "v3", platforms: ["xiaohongshu"] });
    const updated = await markPublished(drafts[0].id);
    expect(updated.status).toBe("published");
  });

  it("DELETE：移除后记录列表不再含该 id；未知 id → 404", async () => {
    const drafts = await createPublishDrafts({ source_kind: "video", source_task_id: "v4", platforms: ["wechat_channels"] });
    const res = await deletePublishRecord(drafts[0].id);
    expect(res.deleted).toBe(true);
    const records = await listPublishRecords();
    expect(records.find((r) => r.id === drafts[0].id)).toBeUndefined();

    let caught: unknown;
    try {
      await deletePublishRecord("nope");
    } catch (e) {
      caught = e;
    }
    expect((caught as ApiError).status).toBe(404);
  });
});
