import { apiFetch } from "@/lib/api/client";
import { billingHeaders } from "@/lib/api/billing";
import type {
  BillingConfirmation,
  BillingQuote,
  CutoutBatchRequest,
  CutoutBatchResponse,
  CutoutRequest,
  CutoutResponse,
  ModelBatchRequest,
  ModelBatchResponse,
  ModelRequest,
  ModelResponse,
  ModelStyle,
  ModelStylesResponse,
  PosterBatchRequest,
  PosterBatchResponse,
  PosterRequest,
  PosterResponse,
  PosterTemplate,
  PosterTemplatesResponse
} from "@/lib/api/types";

/** 获取与单张或批量抠图请求逐字段绑定的服务端报价。 */
export function estimateEcomCutout(
  body: CutoutRequest | CutoutBatchRequest
): Promise<BillingQuote> {
  return apiFetch<BillingQuote>("/api/v1/ecom-images/cutout/estimate", {
    method: "POST",
    body
  });
}

/** 按已确认的服务端报价提交抠图；批量体只路由到严格的 batch 端点。 */
export function createEcomCutout(
  body: CutoutRequest,
  confirmation: BillingConfirmation
): Promise<CutoutResponse>;
export function createEcomCutout(
  body: CutoutBatchRequest,
  confirmation: BillingConfirmation
): Promise<CutoutBatchResponse>;
export function createEcomCutout(
  body: CutoutRequest | CutoutBatchRequest,
  confirmation: BillingConfirmation
): Promise<CutoutResponse | CutoutBatchResponse> {
  const path = "items" in body
    ? "/api/v1/ecom-images/cutout/batch"
    : "/api/v1/ecom-images/cutout";
  return apiFetch<CutoutResponse | CutoutBatchResponse>(path, {
    method: "POST",
    body,
    headers: billingHeaders(confirmation)
  });
}

/** 获取与单张或批量 AI 模特请求逐字段绑定的服务端报价。 */
export function estimateEcomModel(
  body: ModelRequest | ModelBatchRequest
): Promise<BillingQuote> {
  return apiFetch<BillingQuote>("/api/v1/ecom-images/model/estimate", {
    method: "POST",
    body
  });
}

/** 按已确认的服务端报价提交 AI 模特任务。 */
export function createEcomModel(
  body: ModelRequest,
  confirmation: BillingConfirmation
): Promise<ModelResponse>;
export function createEcomModel(
  body: ModelBatchRequest,
  confirmation: BillingConfirmation
): Promise<ModelBatchResponse>;
export function createEcomModel(
  body: ModelRequest | ModelBatchRequest,
  confirmation: BillingConfirmation
): Promise<ModelResponse | ModelBatchResponse> {
  const path = "items" in body
    ? "/api/v1/ecom-images/model/batch"
    : "/api/v1/ecom-images/model";
  return apiFetch<ModelResponse | ModelBatchResponse>(path, {
    method: "POST",
    body,
    headers: billingHeaders(confirmation)
  });
}

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

/**
 * 电商图扩展 Phase3 (ECOM-POSTER-UI-0001) — 营销海报。单张/批量返回已创建 photo
 * VideoTask(kind=ecom_poster)的 task_id,经 tasks-context.trackExisting 轮询。
 */

/** 营销海报版式预设列表(GET)。 */
export async function listPosterTemplates(): Promise<PosterTemplate[]> {
  const res = await apiFetch<PosterTemplatesResponse>("/api/v1/ecom-images/poster-templates", { method: "GET" });
  return res?.templates ?? [];
}

/** 单张营销海报 → { task_id, status }。 */
export function posterImage(body: PosterRequest): Promise<PosterResponse> {
  return apiFetch<PosterResponse>("/api/v1/ecom-images/poster", { method: "POST", body });
}

/** 批量营销海报 → fan-out N(clamp 1..20)个 photo 任务,返 { batch_id, tasks:[{task_id,...}] }。 */
export function posterImageBatch(body: PosterBatchRequest): Promise<PosterBatchResponse> {
  return apiFetch<PosterBatchResponse>("/api/v1/ecom-images/poster/batch", { method: "POST", body });
}
