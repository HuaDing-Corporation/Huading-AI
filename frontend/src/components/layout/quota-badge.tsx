"use client";

import { useQuota } from "@/lib/api/hooks";

// Real quota (GET /quota). Renders nothing while unloaded so we never show a
// fake number. Neutral glass pill (token-driven, AA-safe), not a heavy gold pill.
export function QuotaBadge() {
  const { data } = useQuota();
  if (!data) return null;
  return (
    <div className="flex max-w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-field border border-line-gold bg-glass-soft px-3 py-1.5 text-[11.5px] text-ink-soft">
      <span className="font-medium text-ink">当前余额 {data.remaining}/{data.total}</span>
      {!data.has_active_subscription && <span>当前无生效订阅</span>}
      <span>运行任务冻结 {data.reserved}</span>
      <span>人工交付冻结 {data.manual_fulfillment_held_credits}</span>
      <span>待下期到账 {data.pending_refund_credits}</span>
    </div>
  );
}
