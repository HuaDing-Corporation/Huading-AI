import { apiFetch } from "@/lib/api/client";
import type {
  CutoutBatchRequest,
  CutoutBatchResponse,
  CutoutRequest,
  CutoutResponse,
  ModelBatchRequest,
  ModelBatchResponse,
  ModelRequest,
  ModelResponse,
  ModelStyle,
  ModelStylesResponse
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

/**
 * 电商图扩展 Phase2 (ECOM-MODEL-UI-0001) — AI 模特。单张/批量同样返回已创建 photo
 * VideoTask(kind=ecom_model)的 task_id,经 tasks-context.trackExisting 轮询。
 */

/** AI 模特风格预设列表(GET)。 */
export async function listModelStyles(): Promise<ModelStyle[]> {
  const res = await apiFetch<ModelStylesResponse>("/api/v1/ecom-images/model-styles", { method: "GET" });
  return res?.styles ?? [];
}

/** 单张 AI 模特 → { task_id, status }。 */
export function modelImage(body: ModelRequest): Promise<ModelResponse> {
  return apiFetch<ModelResponse>("/api/v1/ecom-images/model", { method: "POST", body });
}

/** 批量 AI 模特 → fan-out N(clamp 1..20)个 photo 任务,返 { batch_id, tasks:[{task_id,...}] }。 */
export function modelImageBatch(body: ModelBatchRequest): Promise<ModelBatchResponse> {
  return apiFetch<ModelBatchResponse>("/api/v1/ecom-images/model/batch", { method: "POST", body });
}
