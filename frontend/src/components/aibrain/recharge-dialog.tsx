"use client";

// 华鼎AI智脑 · 充值弹窗（AIBRAIN-UI-0001）。档位 100/500/1000/2000（D4）；**必须明示「单向不可退」**（D4/任务包 §3）。
// 复用 ui/dialog + ContactDialog 的关闭约定（DialogClose asChild → Button ghost icon）。

import { useState } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import { ApiError } from "@/lib/api/client";
import { useRecharge } from "@/lib/aibrain/hooks";
import { RECHARGE_TIERS } from "@/lib/aibrain/types";

export function RechargeDialog({
  open,
  onOpenChange,
  onRecharged
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRecharged?: (balance: number) => void;
}) {
  const recharge = useRecharge();
  const [amount, setAmount] = useState<number>(RECHARGE_TIERS[1]);
  const [error, setError] = useState<string | null>(null);

  const onConfirm = async () => {
    setError(null);
    try {
      const res = await recharge.mutateAsync(amount);
      onRecharged?.(res.balance);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : copy.aibrain.rechargeFailed);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next && !recharge.isPending) onOpenChange(false); }}>
      <DialogContent className="w-[min(92vw,440px)]">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <DialogTitle className="text-base font-semibold text-ink">{copy.aibrain.rechargeTitle}</DialogTitle>
            <DialogDescription className="mt-1 text-[12.5px] text-ink-soft">{copy.aibrain.rechargeDesc}</DialogDescription>
          </div>
          <DialogClose asChild>
            <Button variant="ghost" size="icon" aria-label={copy.common.close} className="flex-none" disabled={recharge.isPending}>
              <X size={16} strokeWidth={2} />
            </Button>
          </DialogClose>
        </div>

        <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label={copy.aibrain.rechargeTitle}>
          {RECHARGE_TIERS.map((tier) => {
            const selected = tier === amount;
            return (
              <button
                key={tier}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => setAmount(tier)}
                className={cn(
                  "rounded-field border px-4 py-3 text-center text-sm tabular-nums outline-none transition-colors focus-visible:shadow-focus-gold",
                  selected
                    ? "border-line-sel bg-chip-sel text-ink shadow-mark"
                    : "border-line-gold bg-glass-fill text-ink-soft hover:bg-glass-hover"
                )}
              >
                {copy.aibrain.rechargeAmount(tier)}
              </button>
            );
          })}
        </div>

        {/* 🔴 单向不可退 —— 明示（D4）。用 error 语义色 + 文字，不靠颜色单一。 */}
        <p className="mt-4 rounded-field bg-error-bg px-3 py-2 text-[12.5px] leading-relaxed text-error-fg">
          {copy.aibrain.rechargeIrreversible}
        </p>

        {error && (
          <p role="alert" className="mt-3 rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
            {error}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="soft" size="sm" onClick={() => onOpenChange(false)} disabled={recharge.isPending}>
            {copy.common.cancel}
          </Button>
          <Button variant="primary" size="sm" onClick={onConfirm} disabled={recharge.isPending}>
            {recharge.isPending ? copy.common.processing : copy.aibrain.rechargeConfirm}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
