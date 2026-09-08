"use client";

import { useQuota } from "@/lib/api/hooks";

// Real quota (GET /quota). Renders nothing while unloaded so we never show a
// fake number. Neutral glass pill (token-driven, AA-safe), not a heavy gold pill.
export function QuotaBadge() {
  const { data } = useQuota();
  if (!data) return null;
  return (
    <div className="max-w-full rounded-field border border-line-gold bg-glass-soft px-3 py-1.5 text-[11.5px] text-ink-soft">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-medium text-ink">当前余额 {data.remaining}/{data.total}</span>
        {!data.has_active_subscription && <span>当前无生效订阅</span>}
        <span>当前订阅冻结总额 {data.reserved}</span>
        <span>人工交付冻结（跨订阅） {data.manual_fulfillment_held_credits}</span>
        <span>待下期到账 {data.pending_refund_credits}</span>
      </div>
      <p className="mt-1">人工交付冻结与当前订阅冻结可能重叠，不相加。</p>
    </div>
  );
}
