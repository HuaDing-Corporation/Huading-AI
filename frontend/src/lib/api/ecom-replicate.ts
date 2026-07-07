import { apiFetch } from "@/lib/api/client";

// 电商详情图·强制复刻工作流 adapter（ECOM-REPLICATE-UI-0001）—— 收拢类型 + fetch，组件只依赖本模块。
// **mock 先行**：端点/字段形状以 BE 包 ECOM-REPLICATE-BE-0001 回执为准；BE 合并后仅调本文件对齐、组件不动。
// 两阶段状态机（provider-neutral）：plan(规划表 + total_credits) → confirm(一次扣费) → 生成轮询 → 结果/单张重试。

export type EcomReplicateMode = "main" | "detail"; // 主图(5张) / 详情页(12张)
export type EcomReplicateJobStatus =
  | "planning"
  | "plan_ready"
  | "generating"
  | "completed"
  | "partial_failed"
  | "failed";
export type EcomOutputStatus = "pending" | "generating" | "succeeded" | "failed";

/** 规划表一行（§10：页码/主题/对应参考图/主副标题/展示方式/尺寸/「原图输出不裁剪」）。 */
export interface EcomReplicatePlanItem {
  page_no: number;
  theme: string; // 页面主题
  ref_label: string; // 对应参考图（ref_001 / 白底图）
  main_title: string; // 主标题
  sub_title: string; // 副标题 / 卖点
  display_style: string; // 商品展示方式
  requested_size: string; // gpt-image-2 请求尺寸 1024x1024 / 768x1024
  no_crop_notice: string; // 「原图输出，不裁剪」
  reused: boolean; // 参考图不足 → 循环复用标注（前端如实展示）
}

/** 每张输出（§二.阶段二）：记 requested vs actual 尺寸（APIMart 非精确像素）+ 原图 asset/下载。 */
export interface EcomReplicateOutput {
  page_no: number;
  status: EcomOutputStatus;
  asset_id?: string | null;
  download_url?: string | null; // 原图 bytes（禁前端后处理）
  preview_url?: string | null;
  requested_size: string;
  actual_dimensions?: string | null; // APIMart 实返（如 1086x1448）；null=未出
  error_code?: string | null;
}

export interface EcomReplicateJob {
  job_id: string;
  status: EcomReplicateJobStatus;
  output_mode: EcomReplicateMode;
  total_credits: number; // **后端取，前端不硬编码**（主图 75 / 详情 180 由 BE 费率算）
  plan: EcomReplicatePlanItem[];
  outputs: EcomReplicateOutput[]; // planning/plan_ready 阶段为空 []；生成后逐张填充
}

/** 阶段一·规划请求体（只收 asset_id + 文案 + 模式，复用现有图片上传拿 asset_id）。 */
export interface EcomReplicatePlanInput {
  output_mode: EcomReplicateMode;
  reference_image_asset_ids: string[]; // 参考图 1+ 张
  product_image_asset_ids: string[]; // 商品图 1+ 张
  product_info: string; // 商品信息
  selling_points: string[]; // 核心卖点（多条）
  size?: string; // 可选，缺省由 BE 按模式定
}

const BASE = "/api/v1/ecom-images/detail"; // 以 BE 包为准（mock 先行）

/** 阶段一：分析建模板 → 返回规划表 + total_credits（status=plan_ready）。不扣费。 */
export function planEcomReplicate(input: EcomReplicatePlanInput): Promise<EcomReplicateJob> {
  return apiFetch<EcomReplicateJob>(`${BASE}/plan`, { method: "POST", body: input });
}

/** 确认：一次性扣费 total_credits → 落生成任务（status=generating）。二次确认幂等（已确认不重复扣）。 */
export function confirmEcomReplicate(jobId: string): Promise<EcomReplicateJob> {
  return apiFetch<EcomReplicateJob>(`${BASE}/${encodeURIComponent(jobId)}/confirm`, { method: "POST" });
}

/** 轮询整套任务状态（前端在全部完成前只显进度、禁分批展示）。 */
export function getEcomReplicateJob(jobId: string): Promise<EcomReplicateJob> {
  return apiFetch<EcomReplicateJob>(`${BASE}/${encodeURIComponent(jobId)}`, { method: "GET" });
}

/** 质检失败单张重试（走后端 retry，**不重复扣费**）。 */
export function retryEcomReplicateOutput(jobId: string, pageNo: number): Promise<EcomReplicateJob> {
  return apiFetch<EcomReplicateJob>(
    `${BASE}/${encodeURIComponent(jobId)}/outputs/${pageNo}/retry`,
    { method: "POST" }
  );
}

/** 全部输出是否终态（用于「全部完成才一次性展示」判定）。 */
export function isEcomReplicateSettled(job: EcomReplicateJob): boolean {
  return job.status === "completed" || job.status === "partial_failed" || job.status === "failed";
}
