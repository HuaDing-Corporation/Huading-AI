import { ApiError, apiFetch, apiUrl, authHeaders } from "@/lib/api/client";
import { parseBrandVoiceOrderResource } from "@/lib/api/billing";

// 管理员后台 adapter（ADMIN-CONSOLE-UI-0001 · FIX1 已按**真实 BE #165** 逐字段对齐）。
// 契约源：backend/app/schemas/admin_console.py + routes/admin_console.py（merge 618d7b94）。
// 路由前缀 /api/v1/admin/console，统一 require_platform_admin → 403 PLATFORM_ADMIN_REQUIRED。
// 分页一律 page/page_size（响应含 page/page_size）；错误 message 为 BE 中文，UI 原样展示。

const BASE = "/api/v1/admin/console";

export type PlanCode = "free" | "basic" | "huading";
export type TenantStatus = "active" | "suspended" | "closed";
export type AdminTaskFamily = "video" | "reverse_prompt" | "ecom_replicate";
export type AdminTaskStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type AuditAction = "credits_adjust" | "plan_change" | "status_change" | "voice_slot_assign" | "task_retry";
export type AdminBrandVoiceOrderStatus = "awaiting_fulfillment" | "fulfilled" | "rejected";
export type AdminBrandVoiceOrderAction =
  | { action: "fulfill"; provider_voice_id: string }
  | { action: "reject"; rejection_reason: string };

export interface AdminBrandVoiceOrderRead {
  id: string;
  tenant_id: string;
  ordered_by_user_id: string;
  order_type: "create" | "renew";
  requested_name: string;
  source_audio_asset_id: string;
  existing_brand_voice_id: string | null;
  status: AdminBrandVoiceOrderStatus;
  fulfilled_brand_voice_id: string | null;
  fulfilled_provider_voice_id: string | null;
  rejection_reason: string | null;
  fulfilled_at: string | null;
  expires_at: string | null;
  rejected_at: string | null;
  created_at: string;
  updated_at: string;
  billing: import("@/lib/api/types").BillingSummary;
  refund_disposition: "not_applicable" | "source_subscription_released" | "current_subscription_credited" | "pending_next_subscription";
  refund_grant_status: "pending" | "applied" | null;
  refund_applied_at: string | null;
  source_audio_url?: string | null;
}

export interface AdminBrandVoiceOrderPage {
  items: AdminBrandVoiceOrderRead[];
  total: number;
  page: number;
  page_size: number;
}

function invalidBrandVoiceOrderResponse(): never {
  throw new ApiError("品牌音色订单响应不符合契约。", "INVALID_BRAND_VOICE_ORDER_RESPONSE", 502);
}

function parseAdminBrandVoiceOrder(value: unknown, allowSourceAudioUrl = false): AdminBrandVoiceOrderRead {
  return parseBrandVoiceOrderResource(value, { allowSourceAudioUrl }) ?? invalidBrandVoiceOrderResponse();
}

/** 订阅额度快照（BE AdminSubscriptionSnapshot）。 */
export interface AdminSubscriptionSnapshot {
  id: string;
  total: number;
  used: number;
  reserved: number;
  remaining: number;
}

/** 租户列表行（BE AdminTenantItem——owner_email/plan_code/subscription 均可空）。 */
export interface AdminTenantRow {
  tenant_id: string;
  slug: string;
  name: string;
  status: TenantStatus | string;
  created_at: string;
  owner_email: string | null;
  plan_code: PlanCode | string | null;
  subscription: AdminSubscriptionSnapshot | null;
  task_count: number;
}

export interface AdminTenantPage {
  items: AdminTenantRow[];
  total: number;
  page: number;
  page_size: number;
}

export type AdminTenantSortField = "credits_used" | "created_at" | "balance";
export type AdminSortOrder = "asc" | "desc";

export interface AdminTenantListQuery {
  q?: string;
  plan?: PlanCode | "";
  status?: TenantStatus | "";
  sort?: AdminTenantSortField;
  order?: AdminSortOrder;
  page: number;
  page_size: number;
}

/** 用量流水行（BE AdminUsageItem）。 */
export interface AdminUsageRow {
  id: string;
  created_at: string;
  tenant_id: string;
  tenant_slug: string;
  tenant_name: string;
  capability: string;
  provider: string;
  model: string | null;
  quantity: number;
  unit: string;
  credits: number;
  cost_cents: number;
  status: "reserved" | "settled" | "released" | string;
  video_task_id: string | null;
}

export interface AdminUsagePage {
  items: AdminUsageRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface AdminUsageQuery {
  tenant_id?: string;
  from?: string;
  to?: string;
  capability?: string;
  provider?: string;
  status?: string;
  page: number;
  page_size: number;
}

/** 任务监控行（BE AdminTaskItem——retryable 由 BE 判定，前端不自判 status）。 */
export interface AdminTaskRow {
  id: string;
  task_family: AdminTaskFamily;
  tenant_id: string;
  tenant_slug: string;
  tenant_name: string;
  mode: string;
  label: string | null;
  video_mode: string | null;
  status: AdminTaskStatus;
  progress: number | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  retryable: boolean;
}

export interface AdminTaskPage {
  items: AdminTaskRow[];
  total: number;
  page: number;
  page_size: number;
}

/** 音色槽位（BE AdminVoiceSlotItem——平台池与租户专属合一列表，scope 区分）。 */
export interface AdminVoiceSlotItem {
  speaker_id: string;
  scope: "platform" | "tenant";
  sources: string[];
  tenant_id: string | null;
  tenant_slug: string | null;
  tenant_name: string | null;
  occupied: boolean;
  brand_voice_id: string | null;
  brand_voice_name: string | null;
  brand_voice_status: string | null;
}

export interface AdminVoiceSlots {
  items: AdminVoiceSlotItem[];
  total: number;
  remaining: number;
}

/** 审计日志行（BE AdminAuditLogItem——before/after 可为 null）。 */
export interface AdminAuditRow {
  id: string;
  actor_user_id: string;
  actor_email: string | null;
  actor_tenant_id: string;
  action: AuditAction | string;
  target_tenant_id: string | null;
  target_tenant_slug: string | null;
  target_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
  created_at: string;
}

export interface AdminAuditPage {
  items: AdminAuditRow[];
  total: number;
  page: number;
  page_size: number;
}

/**
 * 重试回执（BE AdminTaskRetryResponse，202）——FIX1 冻结三态，**无 estimate_basis 字段**：
 * charged=false → 不重复扣费；charged=true+is_estimate=false → credits 实扣（固定价）；
 * charged=true+is_estimate=true → credits 为预计（仅 released avatar_talk，按实际成片时长结算）。
 */
export interface AdminRetryReceipt {
  id: string;
  task_family: AdminTaskFamily;
  tenant_id: string;
  status: "queued";
  progress: number;
  charged: boolean;
  credits: number;
  is_estimate: boolean;
}

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") sp.set(key, String(value));
  }
  return sp.toString();
}

export function fetchAdminTenants(query: AdminTenantListQuery): Promise<AdminTenantPage> {
  return apiFetch<AdminTenantPage>(`${BASE}/tenants?${qs({ ...query })}`, { method: "GET" });
}

export interface AdminTenantDetail {
  tenant: AdminTenantRow;
  recent_tasks: AdminTaskRow[];
  recent_usage: AdminUsageRow[];
  voice_slots: AdminVoiceSlotItem[];
}

export function fetchAdminTenantDetail(tenantId: string): Promise<AdminTenantDetail> {
  return apiFetch<AdminTenantDetail>(`${BASE}/tenants/${encodeURIComponent(tenantId)}`, { method: "GET" });
}

/** 余额增减（delta≠0 + reason 1–500 必填）→ {tenant_id, delta, subscription}。下限保护 422 CREDIT_TOTAL_BELOW_COMMITTED（中文原样展示）。 */
export function adjustTenantCredits(
  tenantId: string,
  input: { delta: number; reason: string }
): Promise<{ tenant_id: string; delta: number; subscription: AdminSubscriptionSnapshot }> {
  return apiFetch(`${BASE}/tenants/${encodeURIComponent(tenantId)}/credits`, { method: "POST", body: input });
}

/** 改套餐（PATCH）。平台租户降级 → 422 CANNOT_DOWNGRADE_PLATFORM_TENANT。 */
export function changeTenantPlan(
  tenantId: string,
  planCode: PlanCode,
  reason?: string
): Promise<{ tenant_id: string; plan_code: PlanCode; subscription: AdminSubscriptionSnapshot }> {
  return apiFetch(`${BASE}/tenants/${encodeURIComponent(tenantId)}/plan`, {
    method: "PATCH",
    body: { plan_code: planCode, ...(reason ? { reason } : {}) }
  });
}

/** 启用/停用（PATCH {active:bool}）→ status active/suspended。停用平台租户 → 422 CANNOT_SUSPEND_PLATFORM_TENANT。 */
export function changeTenantStatus(
  tenantId: string,
  active: boolean,
  reason?: string
): Promise<{ tenant_id: string; status: TenantStatus }> {
  return apiFetch(`${BASE}/tenants/${encodeURIComponent(tenantId)}/status`, {
    method: "PATCH",
    body: { active, ...(reason ? { reason } : {}) }
  });
}

export function fetchAdminVoiceSlots(): Promise<AdminVoiceSlots> {
  return apiFetch<AdminVoiceSlots>(`${BASE}/voice-slots`, { method: "GET" });
}

export async function listAdminBrandVoiceOrders(query: {
  status?: AdminBrandVoiceOrderStatus | "";
  page: number;
  page_size: number;
}): Promise<AdminBrandVoiceOrderPage> {
  const page = await apiFetch<unknown>(`${BASE}/brand-voice-orders?${qs(query)}`, { method: "GET" });
  if (
    typeof page !== "object" || page === null || Array.isArray(page) ||
    Object.keys(page).length !== 4 ||
    !["items", "total", "page", "page_size"].every((key) => Object.prototype.hasOwnProperty.call(page, key))
  ) invalidBrandVoiceOrderResponse();
  const candidate = page as Record<string, unknown>;
  if (!Array.isArray(candidate.items) || !Number.isSafeInteger(candidate.total) || !Number.isSafeInteger(candidate.page) || !Number.isSafeInteger(candidate.page_size)) invalidBrandVoiceOrderResponse();
  return {
    items: candidate.items.map((item) => parseAdminBrandVoiceOrder(item)),
    total: candidate.total as number,
    page: candidate.page as number,
    page_size: candidate.page_size as number
  };
}

export async function getAdminBrandVoiceOrder(orderId: string): Promise<AdminBrandVoiceOrderRead> {
  return parseAdminBrandVoiceOrder(
    await apiFetch<unknown>(`${BASE}/brand-voice-orders/${encodeURIComponent(orderId)}`, { method: "GET" }),
    true
  );
}

export async function resolveAdminBrandVoiceOrder(
  orderId: string,
  action: AdminBrandVoiceOrderAction
): Promise<AdminBrandVoiceOrderRead> {
  const value = await apiFetch<unknown>(`${BASE}/brand-voice-orders/${encodeURIComponent(orderId)}/resolve`, {
    method: "POST",
    body: action
  });
  return parseAdminBrandVoiceOrder(value);
}

/** doubao speaker_id 前端预校验（BE pattern 权威）。 */
export const SPEAKER_ID_PATTERN = /^S_[A-Za-z0-9_-]{1,157}$/;

/** BE AdminTaskStatus 用 succeeded（非 done）——映射到既有 StatusBadge 视觉档（tasks 页 / 租户详情共用）。 */
export const TASK_BADGE_STATUS: Record<AdminTaskStatus, "queued" | "running" | "done" | "failed" | "cancelled"> = {
  queued: "queued",
  running: "running",
  succeeded: "done",
  failed: "failed",
  cancelled: "cancelled"
};

/** 给租户挂 speaker 槽位（POST /tenants/{id}/voice-slots，幂等：重复挂 changed:false 不报错）。 */
export function assignVoiceSlot(
  tenantId: string,
  input: { speaker_id: string; reason?: string }
): Promise<{ tenant_id: string; speaker_id: string; changed: boolean; speaker_ids: string[] }> {
  return apiFetch(`${BASE}/tenants/${encodeURIComponent(tenantId)}/voice-slots`, { method: "POST", body: input });
}

export function fetchAdminUsage(query: AdminUsageQuery): Promise<AdminUsagePage> {
  return apiFetch<AdminUsagePage>(`${BASE}/usage?${qs({ ...query })}`, { method: "GET" });
}

/**
 * 导出用量 CSV（对账）。CSV 裸响应（BOM 前缀 + Content-Disposition）非信封 → 原始 fetch + 鉴权头；
 * 非 2xx 按信封解析（>50000 行 → 422 USAGE_EXPORT_TOO_LARGE，中文 message 原样展示）。
 */
export async function exportAdminUsageCsv(query: Omit<AdminUsageQuery, "page" | "page_size">): Promise<Blob> {
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

export function fetchAdminTasks(query: {
  task_family?: AdminTaskFamily | "";
  tenant_id?: string;
  /** BE Literal 含 done 别名（服务端归一 done→succeeded）；UI 只发真枚举。 */
  status?: AdminTaskStatus | "";
  from?: string;
  to?: string;
  page: number;
  page_size: number;
}): Promise<AdminTaskPage> {
  return apiFetch<AdminTaskPage>(`${BASE}/tasks?${qs({ ...query })}`, { method: "GET" });
}

/** 重跑（POST /tasks/{id}/retry?task_family=，202）。仅 retryable；非法 → 409 TASK_NOT_RETRYABLE / 404 TASK_NOT_FOUND / 503 TASK_RETRY_ENQUEUE_FAILED。 */
export function retryAdminTask(taskId: string, taskFamily?: AdminTaskFamily): Promise<AdminRetryReceipt> {
  const suffix = taskFamily ? `?${qs({ task_family: taskFamily })}` : "";
  return apiFetch<AdminRetryReceipt>(`${BASE}/tasks/${encodeURIComponent(taskId)}/retry${suffix}`, { method: "POST" });
}

/** 审计日志（GET /audit-logs——query 仅 action / target_tenant_id / 分页）。只读。 */
export function fetchAdminAudit(query: {
  action?: AuditAction | "";
  target_tenant_id?: string;
  page: number;
  page_size: number;
}): Promise<AdminAuditPage> {
  return apiFetch<AdminAuditPage>(`${BASE}/audit-logs?${qs({ ...query })}`, { method: "GET" });
}
