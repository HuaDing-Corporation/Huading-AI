import { apiFetch } from "@/lib/api/client";
import type {
  CutoutBatchRequest,
  CutoutBatchResponse,
  CutoutRequest,
  CutoutResponse
} from "@/lib/api/types";

/**
 * 电商图扩展 Phase1 (ECOM-IMG-UI-0001) — 白底图/抠图。同步 REST 提交,返回已创建的
 * photo VideoTask(kind=ecom_cutout)的 task_id,前端经 tasks-context.trackExisting 轮询。
 * 沿用 apiFetch(注入 Bearer + 租户头、解包 M2 封套、抛 ApiError)。
 */

/** 单张抠图 → { task_id, status }。 */
export function cutoutImage(body: CutoutRequest): Promise<CutoutResponse> {
  return apiFetch<CutoutResponse>("/api/v1/ecom-images/cutout", { method: "POST", body });
}

/** 批量抠图 → fan-out N(clamp 1..20)个 photo 任务,返 { batch_id, tasks:[{task_id,...}] }。 */
export function cutoutImageBatch(body: CutoutBatchRequest): Promise<CutoutBatchResponse> {
  return apiFetch<CutoutBatchResponse>("/api/v1/ecom-images/cutout/batch", { method: "POST", body });
}
