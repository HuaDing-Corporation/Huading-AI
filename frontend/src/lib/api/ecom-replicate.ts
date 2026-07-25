import { apiFetch } from "@/lib/api/client";

// 电商详情图·强制复刻工作流 adapter（ECOM-REPLICATE-UI-0001 · FIX1 对齐真实 BE PR #140 + FIX2 GET）。
// 收拢类型 + fetch，组件只依赖本模块。路由前缀 /api/v1/ecom-images/replicate，四端点：
//   POST /replicate                              (201) → EcomReplicateAccepted（创建 + 规划表，不扣费）
//   POST /replicate/{job_id}/confirm             (202) → EcomReplicateConfirmAccepted（minimal，一次扣费）
//   GET  /replicate/{job_id}                     (200) → EcomReplicateAccepted（轮询用，plan.outputs 带每张状态/尺寸）
//   POST /replicate/{job_id}/outputs/{index}/retry(202)→ EcomReplicatePlanOutput（失败单张重试，不重复扣费）
// 契约逐字核对 backend/app/schemas/ecom_images.py::EcomReplicate*（extra="forbid"）。

export type EcomReplicateMode = "main" | "detail"; // 主图(5张) / 详情页(12张)
export type EcomReplicateJobStatus =
  | "planning"
  | "plan_ready"
  | "generating"
  | "completed"
  | "partial_failed"
  | "failed"
  | "cancelled";
export type EcomReplicateOutputStatus = "planned" | "generating" | "succeeded" | "failed";

/** 规划表 / 结果单张（BE EcomReplicatePlanOutput，字段一字不差）。 */
export interface EcomReplicatePlanOutput {
  id: string;
  index: number; // 页序（重试按此定位）
  theme: string; // 页面主题（BE 返机器枚举键，如 layout_match；UI 本地化展示）
  reference_asset_id?: string | null;
  product_asset_id?: string | null;
  requested_size: string; // 请求尺寸 1024x1024 / 768x1024
  requested_aspect: string; // 1:1 / 3:4
  status: EcomReplicateOutputStatus | string;
  prompt?: string | null; // 生成提示词（规划表摘要用）
  asset_id?: string | null; // 生成图资产（succeeded 后）
  // BE FIX2（已定）：GET /replicate/{id} 的 plan.outputs[] 每张带 download_url（未完成 null / succeeded 非空）。
  // **这是唯一的图片 URL**——结果页预览与下载都直接用它（真 BE 无 preview_url；无需 asset_id→URL 解析）。
  download_url?: string | null;
  actual_width?: number | null; // APIMart 实返宽（非精确像素）；null=未出
  actual_height?: number | null; // APIMart 实返高
}

/** 规划载荷（BE EcomReplicatePlanPayload）。分析/映射/计划三 JSON 前端暂不渲染、照收。 */
export interface EcomReplicatePlanPayload {
  outputs: EcomReplicatePlanOutput[];
  reference_analysis_json: Record<string, unknown>[];
  template_mapping_json: Record<string, unknown>;
  generation_plan_json: Record<string, unknown>;
}

/** POST /replicate 与 GET /replicate/{id} 的响应（BE EcomReplicateAccepted）。 */
export interface EcomReplicateJob {
  job_id: string;
  status: EcomReplicateJobStatus;
  output_mode: EcomReplicateMode;
  output_count: number;
  total_credits: number; // **后端算**（output_count × credit_rate），前端不硬编码
  credit_rate: number;
  requested_size: string;
  requested_aspect: string;
  plan: EcomReplicatePlanPayload;
  /**
   * 心跳（GEN-HEARTBEAT-UI-0001 · FIX1 · **通道②**）：详情图**没有 Redis/SSE 通道**（它只有逐张 DB 状态），
   * 所以心跳走**本轮询响应**而不是 SSE——与图片生成那条是两条独立的路，各按各的形态做。
   *
   * 🔴 真形状（BE #222 实测）：schema `str | None`、默认 `None`（`schemas/ecom_images.py:230`）→ 与通道①
   * 相反，这里 **键恒存在**；Redis 不可用时 BE 降级为 `{"heartbeat_at": null}`（HTTP 仍 200，业务轮询不受影响）。
   * 值的字面同通道①：`"2026-07-25T13:16:56.439672+00:00"`（6 位微秒 + `+00:00`）。
   *
   * 前端只把它当"还活着"的**布尔证据**（`!= null`）用来门控计时的显示，**从不解析它的值**——
   * 这样 null / 怪格式在结构上就不可能变成「已 NaN 秒」「Invalid Date」，也绝不因此判失败。
   *
   * 🔴 **没有 `?`**（FIX3）：这是"键恒在、值可空"，不是"可选"。留着 `?` 等于允许 TS 构造一个**真实 BE
   * 永远发不出**的夹具（整键缺失）——#220 那两个用户可见缺陷正是这么来的。把编译器当门用：
   * 谁漏写这个键，tsc 当场报错。⚠️ 与通道①（SSE）**故意不同**：那边是 `heartbeat_at?: string`
   * （可选、无 null，BE 无心跳时整键不出现）。两条通道本来就该长得不一样，别顺手对齐。
   */
  heartbeat_at: string | null;
}

/** POST /confirm 的响应（BE EcomReplicateConfirmAccepted，**不含 plan/outputs**）。 */
export interface EcomReplicateConfirmAccepted {
  job_id: string;
  status: EcomReplicateJobStatus; // generating|completed|partial_failed|failed|cancelled
  output_count: number;
  total_credits: number;
}

/** 规划请求体（BE EcomReplicateRequest，extra=forbid）。product_info 为 **dict**（非字符串）。 */
export interface EcomReplicatePlanInput {
  reference_image_asset_ids: string[]; // 1–4 张
  product_image_asset_ids: string[]; // 1–4 张
  product_info: Record<string, unknown>; // dict，如 { name, description, ... }
  selling_points: string[]; // ≤8 条
  output_mode: EcomReplicateMode;
  size?: string;
}

/** 商品图上限（BE Field max_length=4，不随模式变）。 */
export const ECOM_REPLICATE_MAX_IMAGES = 4;
/**
 * 参考图上限**随出图模式动态**（ECOM-REF-LIMIT-UI-0001，对齐 BE ECOM-REF-LIMIT-BE-0001）：
 * 主图 ≤5、详情/套图 ≤12。商品图仍 4（见上）。
 */
export const ECOM_REPLICATE_REF_MAX: Record<EcomReplicateMode, number> = { main: 5, detail: 12 };
/** 核心卖点上限（BE Field max_length=8）。 */
export const ECOM_REPLICATE_MAX_POINTS = 8;

const BASE = "/api/v1/ecom-images/replicate";

/** 阶段一：创建 + 规划 → 返回规划表 + total_credits（status=plan_ready）。不扣费。 */
export function planEcomReplicate(input: EcomReplicatePlanInput): Promise<EcomReplicateJob> {
  return apiFetch<EcomReplicateJob>(BASE, { method: "POST", body: input });
}

/** 确认：一次性扣费 → 落生成任务（返 minimal，不含 outputs）。二次确认幂等（BE 保证不重复扣）。 */
export function confirmEcomReplicate(jobId: string): Promise<EcomReplicateConfirmAccepted> {
  return apiFetch<EcomReplicateConfirmAccepted>(`${BASE}/${encodeURIComponent(jobId)}/confirm`, {
    method: "POST"
  });
}

/** 轮询整套任务（前端在全部完成前只显进度、禁分批）。GET 返完整 plan.outputs（每张状态/尺寸/资产）。 */
export function getEcomReplicateJob(jobId: string): Promise<EcomReplicateJob> {
  return apiFetch<EcomReplicateJob>(`${BASE}/${encodeURIComponent(jobId)}`, { method: "GET" });
}

/** 质检失败单张重试（按 output.index；**不重复扣费**）。返回该单张最新态（回 planned → 整单回 generating）。 */
export function retryEcomReplicateOutput(jobId: string, outputIndex: number): Promise<EcomReplicatePlanOutput> {
  return apiFetch<EcomReplicatePlanOutput>(
    `${BASE}/${encodeURIComponent(jobId)}/outputs/${outputIndex}/retry`,
    { method: "POST" }
  );
}

/** 整套是否终态（用于「全部完成才一次性展示」判定）。 */
export function isEcomReplicateSettled(status: EcomReplicateJobStatus): boolean {
  return (
    status === "completed" ||
    status === "partial_failed" ||
    status === "failed" ||
    status === "cancelled"
  );
}

/** 单张原始输出尺寸「宽x高」（actual_width/height 均在才给；缺失=null，UI 不得冒充 requested_size）。 */
export function ecomReplicateActualDimensions(output: EcomReplicatePlanOutput): string | null {
  return output.actual_width && output.actual_height
    ? `${output.actual_width}x${output.actual_height}`
    : null;
}
