import type { AnalyticsRange } from "@/lib/api/analytics";

/** 本地日期 → YYYY-MM-DD（用本地年月日，避免 toISOString 的 UTC 偏移把日期挪前一天）。 */
export function ymd(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** 近 n 天区间（含今天）：from = 今天-(n-1)，to = 今天。默认 30 天与后端 resolve_date_range 一致。 */
export function lastNDaysRange(n: number, now: Date = new Date()): AnalyticsRange {
  const to = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const from = new Date(to);
  from.setDate(from.getDate() - (n - 1));
  return { from: ymd(from), to: ymd(to) };
}

/** X 轴短标签 YYYY-MM-DD → MM-DD。 */
export function shortDate(iso: string): string {
  const parts = iso.split("-");
  return parts.length === 3 ? `${parts[1]}-${parts[2]}` : iso;
}

export const DEFAULT_RANGE_DAYS = 30;
