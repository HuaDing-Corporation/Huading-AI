import { apiFetch } from "@/lib/api/client";
import type {
  CreateDraftsRequest,
  CreateDraftsResponse,
  DeleteResult,
  MarkPublishedRequest,
  PublishPlatform,
  PublishPlatformsResponse,
  PublishRecord,
  PublishRecordsResponse
} from "@/lib/api/types";

/**
 * 发布中心 (PUBLISH-UI-0001) 数据层。沿用 apiFetch(鉴权/封套/ApiError)。契约据 seam §1-5 推断，
 * 待对冻结 seam + 真栈校验。「去发布」不在此层(仅前端 window.open 公开 publish_url，不调发布 API)。
 */

/** 平台列表(5)。 */
export async function listPublishPlatforms(): Promise<PublishPlatform[]> {
  const res = await apiFetch<PublishPlatformsResponse>("/api/v1/publish/platforms", { method: "GET" });
  return res?.items ?? [];
}

/** 按所选平台生成草稿(各平台一条记录)。 */
export async function createPublishDrafts(body: CreateDraftsRequest): Promise<PublishRecord[]> {
  const res = await apiFetch<CreateDraftsResponse>("/api/v1/publish/drafts", { method: "POST", body });
  return res?.drafts ?? [];
}

/** 发布记录列表。 */
export async function listPublishRecords(): Promise<PublishRecord[]> {
  const res = await apiFetch<PublishRecordsResponse>("/api/v1/publish/records", { method: "GET" });
  return res?.items ?? [];
}

/** 标记已发布(只改 status)。 */
export function markPublished(id: string): Promise<PublishRecord> {
  return apiFetch<PublishRecord>(`/api/v1/publish/records/${id}`, {
    method: "PATCH",
    body: { status: "published" } satisfies MarkPublishedRequest
  });
}

/** 删除发布记录。 */
export function deletePublishRecord(id: string): Promise<DeleteResult> {
  return apiFetch<DeleteResult>(`/api/v1/publish/records/${id}`, { method: "DELETE" });
}
