"use client";

import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";

import type { AnalyticsRange } from "@/lib/api/analytics";
import { useAnalyticsByTenant } from "@/lib/api/hooks";
import type { AnalyticsTenantSort } from "@/lib/api/types";
import { credits, intFmt, pct, yuan } from "@/lib/analytics/format";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

const LIMIT = 20;
type SortField = "credits" | "cost" | "task_count" | "success_rate";
const cellClass = "px-3 py-2.5 text-[12.5px] align-top";

/** 表头排序状态 → aria-sort 值。 */
function ariaSort(active: boolean, sort: AnalyticsTenantSort): "ascending" | "descending" | "none" {
  if (!active) return "none";
  return sort.endsWith("_asc") ? "ascending" : "descending";
}

function SortableTh({
  label,
  field,
  sort,
  onSort,
  align = "right"
}: {
  label: string;
  field: SortField;
  sort: AnalyticsTenantSort;
  onSort: (field: SortField) => void;
  align?: "left" | "right";
}) {
  const active = sort.startsWith(field + "_");
  const desc = sort === `${field}_desc`;
  const Icon = !active ? ArrowUpDown : desc ? ArrowDown : ArrowUp;
  return (
    <th scope="col" aria-sort={ariaSort(active, sort)} className={cn(cellClass, "font-medium text-ink-soft")}>
      <button
        type="button"
        onClick={() => onSort(field)}
        className={cn(
          "inline-flex items-center gap-1 rounded-mark px-1 py-0.5 outline-none hover:text-ink focus-visible:shadow-focus-gold",
          align === "right" ? "flex-row-reverse" : "",
          active && "text-gold-deep"
        )}
      >
        <Icon size={13} strokeWidth={2} className="flex-none" />
        <span>{label}</span>
      </button>
    </th>
  );
}

/**
 * 租户排行表（ANALYTICS-UI-0001）。表头点选 8 种排序（4 字段 × 升/降），limit/offset 分页，余额四值分列。
 * 移动端表格横向滚动（容器 overflow-x-auto，页面不横滚）。
 */
export function TenantTable({ range }: { range: AnalyticsRange }) {
  const [sort, setSort] = useState<AnalyticsTenantSort>("credits_desc");
  const [offset, setOffset] = useState(0);
  // 区间变化 → 回到第一页（sort 变化在 onSort 内已重置）。
  useEffect(() => {
    setOffset(0);
  }, [range.from, range.to]);

  const query = useAnalyticsByTenant(range, { sort, limit: LIMIT, offset });
  const data = query.data;

  const onSort = (field: SortField) => {
    // 点当前降序列 → 切升序；点其他列或升序列 → 降序（回到第一页）。
    setSort((prev) => (prev === `${field}_desc` ? `${field}_asc` : `${field}_desc`));
    setOffset(0);
  };

  const total = data?.total ?? 0;
  const items = data?.items ?? [];
  const rangeFrom = total === 0 ? 0 : offset + 1;
  const rangeTo = Math.min(offset + LIMIT, total);

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-base font-semibold text-ink">{copy.analytics.tenantTitle}</h2>

      {query.isLoading ? (
        <p className="text-[13px] text-ink-soft">{copy.analytics.loading}</p>
      ) : query.isError ? (
        <div className="text-[13px] text-error-fg">
          {copy.analytics.error}{" "}
          <button type="button" onClick={() => void query.refetch()} className="underline">
            {copy.analytics.retry}
          </button>
        </div>
      ) : items.length === 0 ? (
        <p className="text-[13px] text-ink-soft">{copy.analytics.empty}</p>
      ) : (
        <>
          <div className="min-w-0 overflow-x-auto rounded-field border border-line-gold">
            <table className="w-full min-w-[720px] border-collapse text-left">
              <thead>
                <tr className="border-b border-line-gold bg-glass-soft text-[11.5px]">
                  <th scope="col" className={cn(cellClass, "font-medium text-ink-soft")}>{copy.analytics.colTenant}</th>
                  <SortableTh label={copy.analytics.colCredits} field="credits" sort={sort} onSort={onSort} />
                  <SortableTh label={copy.analytics.colCost} field="cost" sort={sort} onSort={onSort} />
                  <SortableTh label={copy.analytics.colTasks} field="task_count" sort={sort} onSort={onSort} />
                  <SortableTh label={copy.analytics.colSuccessRate} field="success_rate" sort={sort} onSort={onSort} />
                  <th scope="col" className={cn(cellClass, "font-medium text-ink-soft")}>{copy.analytics.colBalance}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((t) => (
                  <tr key={t.tenant_id} className="border-b border-line-gold last:border-none">
                    <td className={cn(cellClass, "text-ink")}>{t.tenant_name}</td>
                    <td className={cn(cellClass, "text-right tabular-nums text-ink")}>{credits(t.credits_used)}</td>
                    <td className={cn(cellClass, "text-right tabular-nums text-gold-deep")}>{yuan(t.cost_cents)}</td>
                    <td className={cn(cellClass, "text-right tabular-nums text-ink")}>{intFmt(t.task_count)}</td>
                    <td className={cn(cellClass, "text-right tabular-nums text-ink")}>{pct(t.success_rate)}</td>
                    <td className={cn(cellClass, "text-ink-soft")}>
                      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] tabular-nums">
                        <span>{copy.analytics.balTotal} {intFmt(t.balance.total)}</span>
                        <span>{copy.analytics.balUsed} {intFmt(t.balance.used)}</span>
                        <span className="text-gold-deep">{copy.analytics.balReserved} {intFmt(t.balance.reserved)}</span>
                        <span className="text-success-fg">{copy.analytics.balRemaining} {intFmt(t.balance.remaining)}</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between text-[12px] text-ink-soft">
            <span className="tabular-nums">{copy.analytics.pageRange(rangeFrom, rangeTo, total)}</span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setOffset((o) => Math.max(0, o - LIMIT))}
                disabled={offset === 0}
                className="rounded-field border border-line-gold bg-glass-fill px-3 py-1 hover:bg-glass-hover disabled:opacity-40"
              >
                {copy.analytics.prevPage}
              </button>
              <button
                type="button"
                onClick={() => setOffset((o) => o + LIMIT)}
                disabled={offset + LIMIT >= total}
                className="rounded-field border border-line-gold bg-glass-fill px-3 py-1 hover:bg-glass-hover disabled:opacity-40"
              >
                {copy.analytics.nextPage}
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
