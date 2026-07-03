"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ShieldAlert } from "lucide-react";

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

/** ApiError 403（后端 require_admin 拒绝）判定。 */
export function isForbidden(err: unknown): boolean {
  return err instanceof ApiError && err.status === 403;
}

function ForbiddenState() {
  return (
    <div className="flex flex-col items-center gap-3 rounded-card border border-line-gold bg-glass-fill px-6 py-16 text-center">
      <ShieldAlert size={40} strokeWidth={1.6} className="text-ink-faint" />
      <h2 className="text-[17px] font-semibold text-ink">{copy.analytics.forbiddenTitle}</h2>
      <p className="text-[13.5px] text-ink-soft">{copy.analytics.forbiddenDesc}</p>
      <Link href="/" className="mt-1 rounded-field border border-line-gold bg-glass-fill px-4 py-2 text-[13px] text-gold-deep hover:bg-glass-hover">
        {copy.analytics.forbiddenBack}
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

  // 非管理员：任一 admin 端点 403 → 整块优雅无权限态（不白屏、不靠前端隐藏兜底）。
  if (isForbidden(overview.error) || isForbidden(reserved.error)) {
    return <ForbiddenState />;
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
 * hydration 的 new Date() 日期错位。四区块以 range 联动刷新；非管理员优雅 403。
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
