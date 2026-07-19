"use client";

// 华鼎AI智脑 · 推理积分余额（AIBRAIN-UI-0001）。显示余额 + 充值入口；余额偏低用**图标+文字**提示（不靠颜色单一）。

import { Coins, Plus, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { copy } from "@/lib/copy";
import { useWallet } from "@/lib/aibrain/hooks";
import { TIERS } from "@/lib/aibrain/types";

/** 低余额阈值：连最低档都预留不起就算低。 */
const LOW_BALANCE = TIERS.low.reserve;

export function WalletBalance({ onRecharge }: { onRecharge: () => void }) {
  const { data: wallet } = useWallet();
  const balance = wallet?.balance ?? 0;
  const low = wallet != null && balance < LOW_BALANCE;

  return (
    <div className="inline-flex items-center gap-2 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5">
      <Coins size={15} strokeWidth={1.8} className="text-gold-deep" aria-hidden />
      <span className="text-[12px] text-ink-soft">{copy.aibrain.balanceLabel}</span>
      <span className="text-[13px] font-semibold tabular-nums text-ink" aria-live="polite">
        {wallet ? balance : "—"}
      </span>
      {low && (
        <span className="inline-flex items-center gap-0.5 text-[11.5px] text-error-fg" role="status">
          <TriangleAlert size={12} strokeWidth={2} aria-hidden />
          {copy.aibrain.balanceLow}
        </span>
      )}
      <Button variant="soft" size="sm" className="ml-1 h-7 px-2.5" onClick={onRecharge}>
        <Plus size={13} strokeWidth={2} /> {copy.aibrain.recharge}
      </Button>
    </div>
  );
}
