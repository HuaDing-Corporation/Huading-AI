"use client";

// 华鼎AI智脑 · 充值弹窗（AIBRAIN-UI-0001）。档位 100/500/1000/2000（D4）；**必须明示「单向不可退」**（D4/任务包 §3）。
// 复用 ui/dialog + ContactDialog 的关闭约定（DialogClose asChild → Button ghost icon）。

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import { ApiError } from "@/lib/api/client";
import { useTopup } from "@/lib/aibrain/hooks";
import { AIBRAIN_ERROR, TOPUP_OPTIONS } from "@/lib/aibrain/types";

export function RechargeDialog({
  open,
  onOpenChange
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const recharge = useTopup();
  const [amount, setAmount] = useState<number>(TOPUP_OPTIONS[1]);
  const [error, setError] = useState<string | null>(null);
  // 🔴 幂等键：一次充值尝试生成一次、**重试复用**（§四之二）。每次打开 = 新尝试 = 新 key；改档位 = 新意图 = 新 key。
  // 🔴🔴 **别改成每次 POST 新生成**（FIX3 §4，CB 复审的设计约束）：BE 的充值幂等依赖 topup **账本行的存续**
  //     （账本被守卫保护为追加式、不可删改，所以幂等成立）。前端若每次重试都新生成 key，就绕过了那条账本去重 →
  //     一次网络重试 = 双倍不可逆扣款（推理积分单向不可退）。键必须由本弹窗**稳定持有、重试复用**。
  // 生成放客户端副作用/交互里（不在 render/SSR 里调 crypto），空则 onConfirm 兜底生成一个。
  const idemKey = useRef<string>("");
  useEffect(() => {
    if (open) idemKey.current = crypto.randomUUID();
  }, [open]);

  const pickAmount = (next: number) => {
    setAmount(next);
    idemKey.current = crypto.randomUUID(); // 改金额 = 新充值意图 = 新 key
  };

  const onConfirm = async () => {
    setError(null);
    if (!idemKey.current) idemKey.current = crypto.randomUUID();
    try {
      // 复用 idemKey.current：上一次失败后再点「确认充值」= 同一 key → 服务端去重、不双扣。
      await recharge.mutateAsync({ amount, idempotencyKey: idemKey.current });
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === AIBRAIN_ERROR.IDEMPOTENCY_KEY_REUSED)
        setError(copy.aibrain.idempotencyReuse);
      else setError(err instanceof ApiError ? err.message : copy.aibrain.rechargeFailed);
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
          {TOPUP_OPTIONS.map((tier) => {
            const selected = tier === amount;
            return (
              <button
                key={tier}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => pickAmount(tier)}
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
