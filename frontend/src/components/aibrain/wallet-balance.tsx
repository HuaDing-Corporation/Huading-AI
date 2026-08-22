"use client";

// 华鼎AI智脑 · 推理积分余额（AIBRAIN-UI-0001）。显示余额 + 充值入口；余额偏低用**图标+文字**提示（不靠颜色单一）。

import { Coins, Plus, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { copy } from "@/lib/copy";
import { useWallet } from "@/lib/aibrain/hooks";
import { formatCreditsExact, minReservationCredits } from "@/lib/aibrain/types";

/**
 * 低余额阈值 = **最低档一次请求的预留下界**（**27.52512**，不是被舍入的 27.5）。
 * 🔴 PRICING-UI-0001：此前是 `TIERS.high.typical`（30），一个「典型消耗」估算值——余额低于它只是
 *    「大概只够再聊一次」。换成预留下界之后，这个提示对应一条**硬边界**：低于它，连最低档都凑不齐
 *    一次预留、发送必被 402 拒。数值上两者相近（30 → 27.52512，视觉几乎不变），但含义从"估算"变成"事实"。
 * ⚠️ 按 low 档而非 high 档取：high 的下界是 137.6256，拿它当阈值会让只用低档的用户长期看到告警。
 */
const LOW_BALANCE = minReservationCredits("low");

export function WalletBalance({ onRecharge }: { onRecharge: () => void }) {
  const { data: wallet } = useWallet();
  const balance = wallet?.available_credits ?? 0;
  const low = wallet != null && balance < LOW_BALANCE;

  return (
    <div className="inline-flex items-center gap-2 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5">
      <Coins size={15} strokeWidth={1.8} className="text-gold-deep" aria-hidden />
      <span className="text-[12px] text-ink-soft">{copy.aibrain.balanceLabel}</span>
      <span className="text-[13px] font-semibold tabular-nums text-ink" aria-live="polite">
        {wallet ? formatCreditsExact(balance) : "—"}
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
