"use client";

import type { AnalyticsOverview } from "@/lib/api/types";
import { credits, intFmt, yuan } from "@/lib/analytics/format";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

const cardClass = "rounded-card border border-line-gold bg-glass-fill px-4 py-3.5";
const labelClass = "text-[12px] tracking-[.5px] text-ink-soft";
const valueBase = "mt-1.5 text-[24px] font-semibold tabular-nums";
const valueClass = `${valueBase} text-ink`;

function Kpi({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "gold" }) {
  return (
    <div className={cardClass}>
      <p className={labelClass}>{label}</p>
      <p className={cn(valueClass, tone === "gold" && "text-gold-deep")}>{value}</p>
      {hint && <p className="mt-0.5 text-[11.5px] text-ink-faint">{hint}</p>}
    </div>
  );
}

/**
 * 概览卡片（ANALYTICS-UI-0001）。总消耗积分 / 总成本¥ / 任务量 / 成功·失败 / 租户数 / 预留积分快照。
 * 成本 = cost_cents/100（不自算毛利）；预留积分（reserved）与已消耗分列，来自 by-tenant 聚合快照。
 */
export function OverviewCards({
  data,
  isLoading,
  isError,
  reservedSum,
  reservedCount,
  reservedComplete
}: {
  data: AnalyticsOverview | undefined;
  isLoading: boolean;
  isError: boolean;
  reservedSum: number | undefined;
  reservedCount: number | undefined;
  reservedComplete: boolean | undefined;
}) {
  if (isLoading) {
    return (
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className={cn(cardClass, "h-[86px] animate-pulse")} aria-hidden />
        ))}
      </div>
    );
  }
  if (isError || !data) {
    return <p role="alert" className="text-[13px] text-error-fg">{copy.analytics.error}</p>;
  }

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <Kpi label={copy.analytics.ovCredits} value={credits(data.total_credits_used)} />
      <Kpi label={copy.analytics.ovCost} value={yuan(data.total_cost_cents)} tone="gold" />
      <Kpi label={copy.analytics.ovTasks} value={intFmt(data.task_count)} hint={copy.analytics.ovTasksHint} />
      {/* 成功 / 失败并列一卡，绿/红语义色 + 文字（非仅色） */}
      <div className={cardClass}>
        <p className={labelClass}>
          {copy.analytics.ovSuccess} / {copy.analytics.ovFailed}
        </p>
        <p className={valueBase}>
          <span className="text-success-fg">{intFmt(data.success_count)}</span>
          <span className="text-ink-faint"> / </span>
          <span className="text-error-fg">{intFmt(data.failed_count)}</span>
        </p>
      </div>
      <Kpi label={copy.analytics.ovTenants} value={intFmt(data.tenant_count)} />
      <Kpi
        label={copy.analytics.ovReserved}
        value={reservedSum === undefined ? "—" : credits(reservedSum)}
        hint={
          reservedCount === undefined
            ? undefined
            : reservedComplete
              ? copy.analytics.ovReservedHintAll(reservedCount)
              : copy.analytics.ovReservedHintTop(reservedCount)
        }
      />
    </div>
  );
}
