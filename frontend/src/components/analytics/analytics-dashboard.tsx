"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Crown } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import type { AnalyticsRange } from "@/lib/api/analytics";
import { useAnalyticsByTenant, useAnalyticsOverview } from "@/lib/api/hooks";
import { DEFAULT_RANGE_DAYS, lastNDaysRange } from "@/lib/analytics/date";
import { DateRangePicker } from "@/components/analytics/date-range-picker";
import { OverviewCards } from "@/components/analytics/overview-cards";
import { TenantTable } from "@/components/analytics/tenant-table";
import { ProviderTable } from "@/components/analytics/provider-table";
import { TrendChart } from "@/components/analytics/trend-chart";
import { copy } from "@/lib/copy";

/** VIP 门禁判定（ADMIN-VIP-GATE-UI-0001）：BE 403 + code=ANALYTICS_PLAN_REQUIRED（非管理员且非 huading plan）。 */
export function isPlanRequired(err: unknown): boolean {
  return err instanceof ApiError && err.status === 403 && err.code === "ANALYTICS_PLAN_REQUIRED";
}

/** 任意 403 兜底（BE 未带 code 的边界）——同样当作无访问权，走同一 VIP 友好页。 */
export function isForbidden(err: unknown): boolean {
  return err instanceof ApiError && err.status === 403;
}

/**
 * VIP 门禁友好页（ADMIN-VIP-GATE-UI-0001）——非管理员且非 huading plan 用户看到此页，不白屏、不透传 403 报错。
 * 视觉与「即将上线」占位页刻意区分：Crown + 金渐变实心圆（VIP 专享质感）vs coming-soon 的 Sparkles + 扁平玻璃圆。
 */
function PlanRequiredState() {
  return (
    <div
      className="flex flex-col items-center gap-3 rounded-card border border-line-gold bg-glass-fill px-6 py-16 text-center"
      role="status"
      aria-live="polite"
    >
      <span className="flex h-14 w-14 items-center justify-center rounded-full bg-grad-gold text-ink shadow-avatar">
        <Crown size={26} strokeWidth={1.8} aria-hidden />
      </span>
      <h2 className="text-[17px] font-semibold text-ink">{copy.analytics.planRequiredTitle}</h2>
      <p className="max-w-sm text-[13.5px] leading-relaxed text-ink-soft">{copy.analytics.planRequiredDesc}</p>
      <Link
        href="/"
        className="mt-1 rounded-field border border-line-gold bg-glass-fill px-4 py-2 text-[13px] text-gold-deep hover:bg-glass-hover"
      >
        {copy.analytics.planRequiredBack}
      </Link>
    </div>
  );
}

export function DashboardInner({ range, onRangeChange }: { range: AnalyticsRange; onRangeChange: (r: AnalyticsRange) => void }) {
  const valid = range.from <= range.to;
  // 非法区间(from>to)不发请求（enabled=valid），避免后端 422 ANALYTICS_INVALID_PERIOD。
  const overview = useAnalyticsOverview(range, valid);
  // 预留积分快照：单独取 by-tenant（limit 100，credits_desc）聚合 reserved，与租户表分页互不干扰。
  const reserved = useAnalyticsByTenant(range, { sort: "credits_desc", limit: 100, offset: 0 }, valid);
  const reservedItems = reserved.data?.items ?? [];
  const reservedSum = reserved.data ? reservedItems.reduce((sum, t) => sum + t.balance.reserved, 0) : undefined;
  const reservedCount = reserved.data ? reservedItems.length : undefined;
  // 口径诚实：仅取消耗榜前 100，故 tenant_count>100 时不是全站预留。complete 决定标签措辞（全量 vs 消耗榜前 N）。
  const reservedComplete =
    reserved.data && overview.data ? reservedItems.length >= overview.data.tenant_count : undefined;

  // VIP 门禁（ADMIN-VIP-GATE-UI-0001）：非管理员且非 huading plan → 任一 admin 端点 403（ANALYTICS_PLAN_REQUIRED
  // 优先，任意 403 兜底）→ 整块渲染 VIP 友好页（不白屏、不透传 403、不靠前端隐藏）。
  if (
    isPlanRequired(overview.error) ||
    isPlanRequired(reserved.error) ||
    isForbidden(overview.error) ||
    isForbidden(reserved.error)
  ) {
    return <PlanRequiredState />;
  }

  return (
    <div className="flex flex-col gap-6">
      <DateRangePicker range={range} onChange={onRangeChange} />
      {valid && (
        <>
          <OverviewCards
            data={overview.data}
            isLoading={overview.isLoading}
            isError={overview.isError}
            reservedSum={reservedSum}
            reservedCount={reservedCount}
            reservedComplete={reservedComplete}
          />
          <TenantTable range={range} />
          <ProviderTable range={range} />
          <TrendChart range={range} />
        </>
      )}
    </div>
  );
}

/**
 * 数据看板编排（ANALYTICS-UI-0001）。range 用 effect 客户端初始化（默认近 30 天），避免 SSR 预渲染与
 * hydration 的 new Date() 日期错位。四区块以 range 联动刷新；非授权（非管理员且非 huading plan）→ 403 优雅友好页。
 */
export function AnalyticsDashboard() {
  const [range, setRange] = useState<AnalyticsRange | null>(null);
  useEffect(() => {
    setRange((prev) => prev ?? lastNDaysRange(DEFAULT_RANGE_DAYS));
  }, []);

  if (!range) {
    return <div className="h-40 animate-pulse rounded-card border border-line-gold bg-glass-soft" aria-hidden />;
  }
  return <DashboardInner range={range} onRangeChange={setRange} />;
}
