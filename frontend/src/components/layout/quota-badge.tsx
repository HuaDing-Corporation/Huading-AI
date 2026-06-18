"use client";

import { useQuota } from "@/lib/api/hooks";

// Real quota (GET /quota). Renders nothing while unloaded so we never show a
// fake number. Neutral glass pill (token-driven, AA-safe), not a heavy gold pill.
export function QuotaBadge() {
  const { data } = useQuota();
  if (!data) return null;
  return (
    <span className="rounded-pill border border-line-gold bg-glass-soft px-3 py-1.5 text-[12px] text-ink-soft">
      余额 {data.remaining}/{data.total}
    </span>
  );
}
