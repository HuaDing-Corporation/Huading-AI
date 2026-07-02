import { apiFetch } from "@/lib/api/client";
import type {
  BatchCreateResponse,
  BatchDetail,
  BatchEstimateResponse,
  BatchJob,
  BatchListResponse,
  BatchRequest
} from "@/lib/api/types";

/**
 * 批量生产中心 (BATCH-PROD-UI-0001) 数据层。沿用 apiFetch(鉴权/封套/ApiError)，零裸 fetch。
 * 契约据 seam《批量生产-方案与API契约冻结-20260702》；后端 BATCH-PROD-0001 合后对齐实际 schema。
 * 表格解析全在前端(SheetJS)→ 提交结构化 rows JSON；后端不碰文件。
 */

/** 整批积分预估（提交前确认用）。 */
export function estimateBatch(body: BatchRequest): Promise<BatchEstimateResponse> {
  return apiFetch<BatchEstimateResponse>("/api/v1/batches/estimate", { method: "POST", body });
}

/** 创建批次（逐行建 VideoTask + 逐条 reserve）；余额不足 422 INSUFFICIENT_CREDITS。 */
export function createBatch(body: BatchRequest): Promise<BatchCreateResponse> {
  return apiFetch<BatchCreateResponse>("/api/v1/batches", { method: "POST", body });
}

/** 批次列表（含进度聚合）。 */
export async function listBatches(limit = 20, offset = 0): Promise<BatchJob[]> {
  const res = await apiFetch<BatchListResponse>(`/api/v1/batches?limit=${limit}&offset=${offset}`, { method: "GET" });
  return res?.items ?? [];
}

/** 批次详情（组视图轮询此端点，间隔 ≥5s）。 */
export function getBatch(id: string): Promise<BatchDetail> {
  return apiFetch<BatchDetail>(`/api/v1/batches/${id}`, { method: "GET" });
}

/** 取消批次：未开跑子任务 cancelled+退分；已跑不中断（best-effort）。 */
export function cancelBatch(id: string): Promise<BatchDetail> {
  return apiFetch<BatchDetail>(`/api/v1/batches/${id}/cancel`, { method: "POST" });
}

/** 单条重试（seam 未定端点，据「失败可单条重试」推断；flag 待后端核）。 */
export function retryBatchTask(batchId: string, taskId: string): Promise<BatchTaskRetryResponse> {
  return apiFetch<BatchTaskRetryResponse>(`/api/v1/batches/${batchId}/tasks/${taskId}/retry`, { method: "POST" });
}
export interface BatchTaskRetryResponse {
  task_id: string;
  status: string;
}
