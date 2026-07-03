import { apiFetch } from "@/lib/api/client";
import type {
  AnalyticsByProvider,
  AnalyticsByTenant,
  AnalyticsGranularity,
  AnalyticsOverview,
  AnalyticsTenantSort,
  AnalyticsTimeseries
} from "@/lib/api/types";

// 管理员数据看板 (ANALYTICS-UI-0001)。全部 GET，单前缀 /api/v1/admin/analytics/*（apiUrl 已防 /api/api）。
// 响应走 ApiResponse 信封，apiFetch 已取 .data。非管理员 → 后端 403 code=FORBIDDEN（页面优雅处理）。
const BASE = "/api/v1/admin/analytics";

export interface AnalyticsRange {
  from: string; // YYYY-MM-DD
  to: string;
}

function qs(params: Record<string, string | number>): string {
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) sp.set(key, String(value));
  return sp.toString();
}

export function fetchAnalyticsOverview(range: AnalyticsRange): Promise<AnalyticsOverview> {
  return apiFetch<AnalyticsOverview>(`${BASE}/overview?${qs({ ...range })}`, { method: "GET" });
}

export function fetchAnalyticsByTenant(
  range: AnalyticsRange,
  opts: { sort: AnalyticsTenantSort; limit: number; offset: number }
): Promise<AnalyticsByTenant> {
  return apiFetch<AnalyticsByTenant>(`${BASE}/by-tenant?${qs({ ...range, ...opts })}`, { method: "GET" });
}

export function fetchAnalyticsByProvider(range: AnalyticsRange): Promise<AnalyticsByProvider> {
  return apiFetch<AnalyticsByProvider>(`${BASE}/by-provider?${qs({ ...range })}`, { method: "GET" });
}

export function fetchAnalyticsTimeseries(
  range: AnalyticsRange,
  granularity: AnalyticsGranularity
): Promise<AnalyticsTimeseries> {
  return apiFetch<AnalyticsTimeseries>(`${BASE}/timeseries?${qs({ ...range, granularity })}`, { method: "GET" });
}
