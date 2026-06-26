import { apiFetch } from "@/lib/api/client";
import type {
  ClearResult,
  CopyDraft,
  CopyDraftCreateRequest,
  CopyDraftListResponse,
  CopyRewriteRequest,
  CopyRewriteResponse,
  CopyTitlesRequest,
  CopyTitlesResponse,
  CopyTopicsRequest,
  CopyTopicsResponse,
  DeleteResult
} from "@/lib/api/types";

/**
 * 文案仿写 + 标题/话题生成 (COPY-UI-0001) — 同步 REST，秒级返回（不进 Celery/SSE/
 * VideoTask）。沿用 apiFetch：注入 Bearer + 租户头、解包 M2 封套 .data、JSON.stringify
 * 自动丢 undefined 字段（smart/custom 不发 n、非 custom 不发 instruction）。
 * 错误经 ApiError 抛出，表单用 errorText() 映射（后端按 seam 已友好化 message，不露 JSON）。
 */

/** 文案改写：smart/custom 返 1 条，auto 返 n 条。 */
export function rewriteCopy(params: CopyRewriteRequest): Promise<CopyRewriteResponse> {
  return apiFetch<CopyRewriteResponse>("/api/v1/copy/rewrite", { method: "POST", body: params });
}

/** 标题候选生成。 */
export function generateTitles(params: CopyTitlesRequest): Promise<CopyTitlesResponse> {
  return apiFetch<CopyTitlesResponse>("/api/v1/copy/titles", { method: "POST", body: params });
}

/** 话题候选生成（带 # 标签）。 */
export function generateTopics(params: CopyTopicsRequest): Promise<CopyTopicsResponse> {
  return apiFetch<CopyTopicsResponse>("/api/v1/copy/topics", { method: "POST", body: params });
}

/** 显式保存一条草稿到历史。 */
export function saveCopyDraft(params: CopyDraftCreateRequest): Promise<CopyDraft> {
  return apiFetch<CopyDraft>("/api/v1/copy/drafts", { method: "POST", body: params });
}

/** 一页草稿，按 offset 分页 — 背书历史「文案」tab（返 total 供分页）。 */
export function listCopyDraftsPage(
  params: { limit?: number; offset?: number } = {}
): Promise<CopyDraftListResponse> {
  const query = new URLSearchParams();
  if (params.limit != null) query.set("limit", String(params.limit));
  if (params.offset != null) query.set("offset", String(params.offset));
  const qs = query.toString();
  return apiFetch<CopyDraftListResponse>(`/api/v1/copy/drafts${qs ? `?${qs}` : ""}`, { method: "GET" });
}

/** 软删单条草稿(deleted_at；现有行为不变，HIST-UI-0001)。 */
export function deleteCopyDraft(id: string): Promise<DeleteResult> {
  return apiFetch<DeleteResult>(`/api/v1/copy/drafts/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** 清空全部草稿(软删该租户全部未删草稿)。 */
export function clearCopyDrafts(): Promise<ClearResult> {
  return apiFetch<ClearResult>("/api/v1/copy/drafts", { method: "DELETE" });
}
