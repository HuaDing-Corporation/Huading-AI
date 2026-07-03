"use client";

import type { AnalyticsRange } from "@/lib/api/analytics";
import { lastNDaysRange } from "@/lib/analytics/date";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

const presets = [
  { days: 7, label: copy.analytics.preset7 },
  { days: 30, label: copy.analytics.preset30 },
  { days: 90, label: copy.analytics.preset90 }
];

const inputClass =
  "rounded-field border border-line-gold bg-glass-fill px-2.5 py-1.5 text-[12.5px] text-ink outline-none focus:border-line-sel focus:shadow-focus-gold";

/**
 * 日期区间选择器（ANALYTICS-UI-0001）。预设 近7/30/90天 + 自定义 from/to；仅在 from≤to 时联动刷新，
 * 否则内联提示（避免把非法区间发给后端触发 422）。受控组件，父持 range。
 */
export function DateRangePicker({
  range,
  onChange
}: {
  range: AnalyticsRange;
  onChange: (range: AnalyticsRange) => void;
}) {
  const invalid = range.from > range.to;

  // 输入始终上抛以更新显示；非法区间(from>to)由下方提示 + 父级不查询兜底，不在此拦截。
  const commit = (next: AnalyticsRange) => onChange(next);
  const activePreset = presets.find((p) => {
    const r = lastNDaysRange(p.days);
    return r.from === range.from && r.to === range.to;
  });

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[12px] tracking-[.5px] text-ink-soft">{copy.analytics.rangeLabel}</span>
        <div className="flex gap-1.5">
          {presets.map((p) => {
            const active = activePreset?.days === p.days;
            return (
              <button
                key={p.days}
                type="button"
                onClick={() => onChange(lastNDaysRange(p.days))}
                aria-pressed={active}
                className={cn(
                  "rounded-pill border px-3 py-1 text-[12px] transition-colors",
                  active
                    ? "border-line-sel bg-chip-sel text-ink"
                    : "border-line-gold bg-glass-fill text-ink-soft hover:bg-glass-hover"
                )}
              >
                {p.label}
              </button>
            );
          })}
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-[12px] text-ink-faint">{copy.analytics.rangeFrom}</label>
          <input
            type="date"
            aria-label={copy.analytics.rangeFrom}
            value={range.from}
            max={range.to}
            onChange={(e) => commit({ ...range, from: e.target.value })}
            className={inputClass}
          />
          <label className="text-[12px] text-ink-faint">{copy.analytics.rangeTo}</label>
          <input
            type="date"
            aria-label={copy.analytics.rangeTo}
            value={range.to}
            min={range.from}
            onChange={(e) => commit({ ...range, to: e.target.value })}
            className={inputClass}
          />
        </div>
      </div>
      {invalid && (
        <p role="alert" className="text-[12px] text-error-fg">
          {copy.analytics.rangeInvalid}
        </p>
      )}
    </div>
  );
}
