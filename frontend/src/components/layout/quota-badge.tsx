"use client";

import { useQuota } from "@/lib/api/hooks";

// Provisional: useQuota returns null until the backend quota endpoint exists.
// Renders nothing rather than a fake number, so we never show misleading data.
export function QuotaBadge() {
  const { data } = useQuota();
  if (!data) return null;
  return (
    <span className="rounded-pill border border-line-gold bg-glass-soft px-3 py-1.5 text-[12px] text-ink-soft">
      额度 {data.used}/{data.limit}
    </span>
  );
}
