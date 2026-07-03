"use client";

import { useState } from "react";

import type { AnalyticsRange } from "@/lib/api/analytics";
import { useAnalyticsTimeseries } from "@/lib/api/hooks";
import type { AnalyticsGranularity, AnalyticsTimeseriesBucket } from "@/lib/api/types";
import { credits, intFmt, yuan } from "@/lib/analytics/format";
import { shortDate } from "@/lib/analytics/date";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

type Metric = "credits" | "cost" | "count";
const METRICS: { key: Metric; label: string; value: (b: AnalyticsTimeseriesBucket) => number; fmt: (n: number) => string }[] = [
  { key: "credits", label: copy.analytics.metricCredits, value: (b) => b.credits_used, fmt: credits },
  { key: "cost", label: copy.analytics.metricCost, value: (b) => b.cost_cents, fmt: yuan },
  { key: "count", label: copy.analytics.metricTasks, value: (b) => b.task_count, fmt: intFmt }
];

// SVG 画布（viewBox 固定，width 100% 自适应；padding 留给坐标轴）。
const W = 800;
const H = 280;
const PAD = { top: 12, right: 14, bottom: 26, left: 52 };
const plotW = W - PAD.left - PAD.right;
const plotH = H - PAD.top - PAD.bottom;

function Toggle<T extends string>({
  options,
  value,
  onChange,
  ariaLabel
}: {
  options: { key: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  ariaLabel: string;
}) {
  return (
    <div role="group" aria-label={ariaLabel} className="inline-flex rounded-pill border border-line-gold bg-glass-fill p-0.5">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          onClick={() => onChange(o.key)}
          aria-pressed={value === o.key}
          className={cn(
            "rounded-pill px-3 py-1 text-[12px] transition-colors",
            value === o.key ? "bg-chip-sel text-ink" : "text-ink-soft hover:text-ink"
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/**
 * 趋势图（ANALYTICS-UI-0001）。轻量自绘 SVG 折线，零图表库依赖。day/week 粒度 + 积分/成本/笔数 指标切换；
 * subtle 网格、X 轴日期自动稀疏、hover/tap tooltip、空态/骨架、屏幕阅读器文本摘要。
 */
export function TrendChart({ range }: { range: AnalyticsRange }) {
  const [granularity, setGranularity] = useState<AnalyticsGranularity>("day");
  const [metric, setMetric] = useState<Metric>("credits");
  const [hover, setHover] = useState<number | null>(null);
  const query = useAnalyticsTimeseries(range, granularity);
  const buckets = query.data?.buckets ?? [];
  const cfg = METRICS.find((m) => m.key === metric)!;

  const values = buckets.map(cfg.value);
  const maxV = Math.max(1, ...values);
  const n = buckets.length;
  const xAt = (i: number) => (n <= 1 ? PAD.left + plotW / 2 : PAD.left + (i / (n - 1)) * plotW);
  const yAt = (v: number) => PAD.top + plotH - (v / maxV) * plotH;
  const linePath = buckets.map((b, i) => `${i === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${yAt(cfg.value(b)).toFixed(1)}`).join(" ");
  const areaPath = n > 0 ? `${linePath} L${xAt(n - 1).toFixed(1)},${PAD.top + plotH} L${xAt(0).toFixed(1)},${PAD.top + plotH} Z` : "";
  // X 轴标签自动稀疏到 ≤7 个。
  const tickStep = Math.max(1, Math.ceil(n / 7));

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-ink">{copy.analytics.trendTitle}</h2>
        <div className="flex flex-wrap gap-2">
          <Toggle
            ariaLabel={copy.analytics.trendTitle}
            options={[
              { key: "day" as AnalyticsGranularity, label: copy.analytics.granDay },
              { key: "week" as AnalyticsGranularity, label: copy.analytics.granWeek }
            ]}
            value={granularity}
            onChange={setGranularity}
          />
          <Toggle ariaLabel={copy.analytics.trendTitle} options={METRICS.map((m) => ({ key: m.key, label: m.label }))} value={metric} onChange={setMetric} />
        </div>
      </div>

      {query.isLoading ? (
        <div className="h-[280px] animate-pulse rounded-field border border-line-gold bg-glass-soft" aria-hidden />
      ) : query.isError ? (
        <div className="text-[13px] text-error-fg">
          {copy.analytics.error}{" "}
          <button type="button" onClick={() => void query.refetch()} className="underline">
            {copy.analytics.retry}
          </button>
        </div>
      ) : n === 0 ? (
        <p className="flex h-[280px] items-center justify-center rounded-field border border-line-gold bg-glass-fill text-[13px] text-ink-soft">
          {copy.analytics.empty}
        </p>
      ) : (
        <div className="rounded-field border border-line-gold bg-glass-fill p-3">
          <p className="sr-only">{copy.analytics.trendSummary(cfg.label, n)}</p>
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: "auto" }} role="img" aria-label={copy.analytics.trendSummary(cfg.label, n)}>
            <defs>
              <linearGradient id="trend-area" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--gold-deep)" stopOpacity="0.22" />
                <stop offset="100%" stopColor="var(--gold-deep)" stopOpacity="0" />
              </linearGradient>
            </defs>
            {/* 水平网格 + Y 轴刻度（0 / 中 / max） */}
            {[0, 0.5, 1].map((f) => {
              const v = maxV * f;
              const y = yAt(v);
              return (
                <g key={f}>
                  <line x1={PAD.left} y1={y} x2={W - PAD.right} y2={y} stroke="var(--bd-gold)" strokeOpacity="0.5" strokeWidth="1" />
                  <text x={PAD.left - 6} y={y + 3} textAnchor="end" className="fill-ink-faint" fontSize="10">
                    {cfg.fmt(v)}
                  </text>
                </g>
              );
            })}
            {/* X 轴日期标签（稀疏） */}
            {buckets.map((b, i) =>
              i % tickStep === 0 || i === n - 1 ? (
                <text key={b.date} x={xAt(i)} y={H - 8} textAnchor="middle" className="fill-ink-faint" fontSize="10">
                  {shortDate(b.date)}
                </text>
              ) : null
            )}
            {areaPath && <path d={areaPath} fill="url(#trend-area)" />}
            <path d={linePath} fill="none" stroke="var(--gold-deep)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
            {buckets.map((b, i) => (
              <circle key={b.date} cx={xAt(i)} cy={yAt(cfg.value(b))} r={hover === i ? 4 : 2.5} fill="var(--gold-deep)">
                <title>{`${b.date} · ${cfg.fmt(cfg.value(b))}`}</title>
              </circle>
            ))}
            {/* 透明命中区 → hover/tap tooltip */}
            {buckets.map((b, i) => (
              <rect
                key={`hit-${b.date}`}
                x={xAt(i) - (n <= 1 ? plotW / 2 : plotW / (n - 1) / 2)}
                y={PAD.top}
                width={n <= 1 ? plotW : plotW / (n - 1)}
                height={plotH}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover((h) => (h === i ? null : h))}
                onTouchStart={() => setHover(i)}
              />
            ))}
            {hover !== null && buckets[hover] && (
              <g pointerEvents="none">
                <line x1={xAt(hover)} y1={PAD.top} x2={xAt(hover)} y2={PAD.top + plotH} stroke="var(--gold-deep)" strokeOpacity="0.35" strokeDasharray="3 3" />
              </g>
            )}
          </svg>
          {hover !== null && buckets[hover] && (
            <p className="mt-1 text-center text-[12px] tabular-nums text-ink-soft" aria-live="polite">
              <span className="text-ink-faint">{buckets[hover].date}</span> · <span className="text-ink">{cfg.fmt(cfg.value(buckets[hover]))}</span>
            </p>
          )}
        </div>
      )}
    </section>
  );
}
