import { ApiError, apiFetch, apiUrl, authHeaders } from "@/lib/api/client";

// 管理员后台 adapter（ADMIN-CONSOLE-UI-0001）。收拢类型 + fetch，组件只依赖本模块。
// 路由前缀 /api/v1/admin/console/*（BE ADMIN-CONSOLE-BE-0001，统一 require_platform_admin → 403
// PLATFORM_ADMIN_REQUIRED）。**mock 先行**：契约按冻结文档（需求冻结-管理员后台网页-20260713）拟定，
// BE 合并后逐字段核对（路径 / 字段名 / 错误码 / CSV 端点 / 重跑扣费口径）再转 ready。

const BASE = "/api/v1/admin/console";

export type PlanCode = "free" | "basic" | "huading";
export type TenantStatus = "active" | "disabled";
export type AdminTaskStatus = "queued" | "running" | "done" | "failed" | "cancelled";
export type AuditAction = "credits_adjust" | "plan_change" | "status_change" | "voice_slot_assign" | "task_retry";

export interface TenantBalance {
  total: number;
  used: number;
  reserved: number;
  remaining: number;
}

/** 租户列表行（GET /tenants）。 */
export interface AdminTenantRow {
  tenant_id: string;
  slug: string;
  name: string;
  owner_email: string;
  plan_code: PlanCode;
  status: TenantStatus;
  /** 平台租户（华鼎AI 自己）——前端据此把「停用」按钮置灰（BE 也会 422，双保险）。 */
  is_platform: boolean;
  balance: TenantBalance;
  created_at: string;
  task_count: number;
}

export interface AdminTenantList {
  items: AdminTenantRow[];
  total: number;
}

export type AdminTenantSort = "created_desc" | "created_asc" | "remaining_desc" | "remaining_asc" | "used_desc" | "used_asc";

export interface AdminTenantListQuery {
  search?: string;
  plan?: PlanCode | "";
  status?: TenantStatus | "";
  sort?: AdminTenantSort;
  limit: number;
  offset: number;
}

/** 租户详情（GET /tenants/{id}）：基础信息 + 最近任务 + 最近用量 + 已挂音色槽位。 */
export interface AdminTenantDetail {
  tenant: AdminTenantRow;
  recent_tasks: { id: string; mode: string; status: AdminTaskStatus; created_at: string }[];
  recent_usage: { created_at: string; capability: string; credits: number }[];
  voice_slots: { speaker_id: string; voice_name: string | null }[];
}

/** 音色槽位总览（GET /voice-slots）。 */
export interface AdminVoiceSlots {
  platform_pool: { speaker_id: string; occupied_by: { tenant_slug: string; voice_name: string | null } | null }[];
  tenant_slots: { tenant_slug: string; speaker_id: string; voice_name: string | null }[];
}

/** 用量流水行（GET /usage）。 */
export interface AdminUsageRow {
  id: string;
  created_at: string;
  tenant_slug: string;
  capability: string;
  provider: string;
  model: string | null;
  quantity: number;
  unit: string;
  credits: number;
  cost_cents: number;
  status: "reserved" | "settled" | "released";
  task_id: string | null;
}

export interface AdminUsageList {
  items: AdminUsageRow[];
  total: number;
}

export interface AdminUsageQuery {
  tenant_id?: string;
  from?: string;
  to?: string;
  capability?: string;
  provider?: string;
  status?: string;
  limit: number;
  offset: number;
}

/** 任务监控行（GET /tasks）。 */
export interface AdminTaskRow {
  id: string;
  tenant_slug: string;
  mode: string;
  status: AdminTaskStatus;
  progress: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
}

export interface AdminTaskList {
  items: AdminTaskRow[];
  total: number;
}

/** 审计日志行（GET /audit）。before/after 为 JSON 快照（余额数字 / 套餐 code / 状态等）。 */
export interface AdminAuditRow {
  id: string;
  created_at: string;
  actor_email: string;
  action: AuditAction;
  target_tenant_slug: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  reason: string | null;
}

export interface AdminAuditList {
  items: AdminAuditRow[];
  total: number;
}

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") sp.set(key, String(value));
  }
  return sp.toString();
}

export function fetchAdminTenants(query: AdminTenantListQuery): Promise<AdminTenantList> {
  return apiFetch<AdminTenantList>(`${BASE}/tenants?${qs({ ...query })}`, { method: "GET" });
}

export function fetchAdminTenantDetail(tenantId: string): Promise<AdminTenantDetail> {
  return apiFetch<AdminTenantDetail>(`${BASE}/tenants/${encodeURIComponent(tenantId)}`, { method: "GET" });
}

/** 余额增减（delta 可正可负 + 理由必填）→ 返回调整后余额。BE 锁订阅 + 下限保护（低于已用+预留 → 422 中文）。 */
export function adjustTenantCredits(tenantId: string, input: { delta: number; reason: string }): Promise<{ balance: TenantBalance }> {
  return apiFetch<{ balance: TenantBalance }>(`${BASE}/tenants/${encodeURIComponent(tenantId)}/credits`, {
    method: "POST",
    body: input
  });
}

export function changeTenantPlan(tenantId: string, planCode: PlanCode): Promise<{ plan_code: PlanCode }> {
  return apiFetch<{ plan_code: PlanCode }>(`${BASE}/tenants/${encodeURIComponent(tenantId)}/plan`, {
    method: "POST",
    body: { plan_code: planCode }
  });
}

export function changeTenantStatus(tenantId: string, status: TenantStatus): Promise<{ status: TenantStatus }> {
  return apiFetch<{ status: TenantStatus }>(`${BASE}/tenants/${encodeURIComponent(tenantId)}/status`, {
    method: "POST",
    body: { status }
  });
}

export function fetchAdminVoiceSlots(): Promise<AdminVoiceSlots> {
  return apiFetch<AdminVoiceSlots>(`${BASE}/voice-slots`, { method: "GET" });
}

/** doubao speaker_id 前端预校验（BE 权威）。 */
export const SPEAKER_ID_PATTERN = /^S_[A-Za-z0-9_-]{1,157}$/;

/** 给租户挂 speaker 槽位（幂等：重复挂不报错）。 */
export function assignVoiceSlot(input: { tenant_id: string; speaker_id: string }): Promise<{ assigned: boolean }> {
  return apiFetch<{ assigned: boolean }>(`${BASE}/voice-slots/assign`, { method: "POST", body: input });
}

export function fetchAdminUsage(query: AdminUsageQuery): Promise<AdminUsageList> {
  return apiFetch<AdminUsageList>(`${BASE}/usage?${qs({ ...query })}`, { method: "GET" });
}

/**
 * 导出用量 CSV（对账）。CSV 是裸文本非 ApiResponse 信封 → 不能走 apiFetch，用带鉴权的原始 fetch；
 * 非 2xx 时按信封解析错误（BE 行数超限 → 422，中文 message 原样展示）。
 */
export async function exportAdminUsageCsv(query: Omit<AdminUsageQuery, "limit" | "offset">): Promise<Blob> {
  const res = await fetch(apiUrl(`${BASE}/usage/export?${qs({ ...query })}`), {
    method: "GET",
    headers: { Accept: "text/csv", ...authHeaders() }
  });
  if (!res.ok) {
    const payload = (await res.json().catch(() => null)) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(payload?.error?.message ?? "导出失败", payload?.error?.code ?? "EXPORT_FAILED", res.status);
  }
  return res.blob();
}

export function fetchAdminTasks(query: { status?: string; tenant_id?: string; from?: string; to?: string; limit: number; offset: number }): Promise<AdminTaskList> {
  return apiFetch<AdminTaskList>(`${BASE}/tasks?${qs({ ...query })}`, { method: "GET" });
}

/**
 * 重跑失败任务的回执披露（FIX1 · BE FIX3 重新冻结的语义）：
 * - charged=false → 本次重试不重新计费（失败时已扣费 / 确认时已扣）；
 * - charged=true + is_estimate=false → credits 为**实扣**（固定价，如视频反推 100）；
 * - charged=true + is_estimate=true → credits 为**预计**（按量任务按实际成片时长结算，最终可能不同），
 *   estimate_basis 为中文结算口径说明（可选）。
 * UI 一律不许静默扣费——横幅按三态分流披露。字段名以 BE 回执为准，合并后逐字段核对。
 */
export interface AdminRetryReceipt {
  task_id: string;
  status: AdminTaskStatus;
  charged: boolean;
  credits: number;
  is_estimate: boolean;
  estimate_basis?: string;
}

export function retryAdminTask(taskId: string): Promise<AdminRetryReceipt> {
  return apiFetch<AdminRetryReceipt>(`${BASE}/tasks/${encodeURIComponent(taskId)}/retry`, { method: "POST" });
}

export function fetchAdminAudit(query: { action?: string; tenant_id?: string; from?: string; to?: string; limit: number; offset: number }): Promise<AdminAuditList> {
  return apiFetch<AdminAuditList>(`${BASE}/audit?${qs({ ...query })}`, { method: "GET" });
}
