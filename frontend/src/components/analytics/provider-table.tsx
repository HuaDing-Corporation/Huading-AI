"use client";

import { Info } from "lucide-react";

import type { AnalyticsRange } from "@/lib/api/analytics";
import { useAnalyticsByProvider } from "@/lib/api/hooks";
import { credits, intFmt, sharePct, yuan } from "@/lib/analytics/format";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

const cellClass = "px-3 py-2.5 text-[12.5px] align-middle";

/**
 * 按功能 / 模型拆分（ANALYTICS-UI-0001）。⚠️ 计数列语义是「计费笔数」(count(UsageRecord))，覆盖文案 /
 * 图片等无 VideoTask 的功能 —— 列名固定「计费笔数」，绝不标「任务量」，防运营误读。占比 = share_pct + 迷你条。
 */
export function ProviderTable({ range }: { range: AnalyticsRange }) {
  const query = useAnalyticsByProvider(range);
  const items = query.data?.items ?? [];

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-base font-semibold text-ink">{copy.analytics.providerTitle}</h2>

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
        <div className="min-w-0 overflow-x-auto rounded-field border border-line-gold">
          <table className="w-full min-w-[640px] border-collapse text-left">
            <thead>
              <tr className="border-b border-line-gold bg-glass-soft text-[11.5px] text-ink-soft">
                <th scope="col" className={cn(cellClass, "font-medium")}>{copy.analytics.colProvider}</th>
                <th scope="col" className={cn(cellClass, "text-right font-medium")}>{copy.analytics.colCredits}</th>
                <th scope="col" className={cn(cellClass, "text-right font-medium")}>{copy.analytics.colCost}</th>
                <th scope="col" className={cn(cellClass, "text-right font-medium")}>
                  <span className="inline-flex items-center gap-1" title={copy.analytics.billingCountHint}>
                    {copy.analytics.colBillingCount}
                    <Info size={12} strokeWidth={2} className="text-ink-faint" aria-hidden />
                  </span>
                </th>
                <th scope="col" className={cn(cellClass, "font-medium")}>{copy.analytics.colShare}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((p, i) => (
                <tr key={`${p.provider}-${p.model ?? ""}-${i}`} className="border-b border-line-gold last:border-none">
                  <td className={cn(cellClass, "text-ink")}>
                    <span className="text-ink">{p.provider}</span>
                    {p.model && <span className="ml-1.5 text-[11.5px] text-ink-faint">{p.model}</span>}
                  </td>
                  <td className={cn(cellClass, "text-right tabular-nums text-ink")}>{credits(p.credits_used)}</td>
                  <td className={cn(cellClass, "text-right tabular-nums text-gold-deep")}>{yuan(p.cost_cents)}</td>
                  <td className={cn(cellClass, "text-right tabular-nums text-ink")}>{intFmt(p.task_count)}</td>
                  <td className={cellClass}>
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-16 flex-none overflow-hidden rounded-pill bg-glass-soft">
                        <div className="h-full rounded-pill bg-grad-gold" style={{ width: `${Math.min(100, Math.max(0, p.share_pct))}%` }} />
                      </div>
                      <span className="tabular-nums text-[12px] text-ink-soft">{sharePct(p.share_pct)}</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
