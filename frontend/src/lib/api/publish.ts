import { apiFetch } from "@/lib/api/client";
import type {
  CreateDraftsRequest,
  CreateDraftsResponse,
  PublishPlatform,
  PublishPlatformId,
  PublishPlatformsResponse,
  PublishRecord,
  PublishRecordsResponse
} from "@/lib/api/types";

/**
 * 发布中心 (PUBLISH-UI-0001) 数据层。沿用 apiFetch(鉴权/封套/ApiError)。逐字对齐后端
 * backend/app/schemas/publish.py + routes/publish.py。「去发布」不在此层(仅前端 window.open
 * 公开 publish_url，不调发布 API)。
 */

/** 平台列表(5，含 title_max/publish_url/cover_ratio/notes)。 */
export async function listPublishPlatforms(): Promise<PublishPlatform[]> {
  const res = await apiFetch<PublishPlatformsResponse>("/api/v1/publish/platforms", { method: "GET" });
  return res?.items ?? [];
}

/** 生成草稿 → { id(记录), items:[各平台可编辑内容] }。 */
export function createPublishDrafts(body: CreateDraftsRequest): Promise<CreateDraftsResponse> {
  return apiFetch<CreateDraftsResponse>("/api/v1/publish/drafts", { method: "POST", body });
}

/** 发布记录列表(嵌套：记录→platforms[]{platform_id,status})。 */
export async function listPublishRecords(): Promise<PublishRecord[]> {
  const res = await apiFetch<PublishRecordsResponse>("/api/v1/publish/records", { method: "GET" });
  return res?.items ?? [];
}

/** 标记某记录下某平台为已发布(PATCH {platform_id, status:"published"})。 */
export function markPublished(recordId: string, platformId: PublishPlatformId): Promise<PublishRecord> {
  return apiFetch<PublishRecord>(`/api/v1/publish/records/${recordId}`, {
    method: "PATCH",
    body: { platform_id: platformId, status: "published" }
  });
}

/** 删除发布记录(整条)。 */
export function deletePublishRecord(id: string): Promise<{ id: string; deleted_at: string }> {
  return apiFetch<{ id: string; deleted_at: string }>(`/api/v1/publish/records/${id}`, { method: "DELETE" });
}
