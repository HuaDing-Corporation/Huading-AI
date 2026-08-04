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
import {
  AIBRAIN_ERROR,
  TOPUP_OPTIONS,
  formatCredits,
  type OutstandingView,
  type ShortfallView
} from "@/lib/aibrain/types";

/**
 * 余额不足说明块（PRICING-UI-0001 §三）—— 只在**因 402 `AIBRAIN_INSUFFICIENT_BALANCE` 而弹开**时渲染。
 *
 * 🔴 §三 要求交代四件事，这里逐条落位：
 *   1 需要多少 → `insufficientRequired`（BE 精确值）/ `insufficientMinRequired`（回退下界 +「至少」）
 *   2 当前多少 → `insufficientAvailable`（拿不到则整行不渲染，**不填 0 冒充**）
 *   3 还差多少 → `insufficientShortfallExact` / `insufficientShortfall`
 *   4 这是临时预留、不是扣费 → `insufficientReserveNote`（**四条里最要紧的一条**：高速档光 completion
 *     就预留 137.6，不说清楚会被当成「一次对话花 137 积分」而吓退用户）
 * 🔴 第 4 条放在**最前面**且用 error 语义色 —— 用户此刻正在看一个被拒的操作，先解释"这笔钱不是花掉了"，
 *    再给数字，顺序反了数字就先造成误解了。
 * 🔴 `exact` 分叉不是措辞洁癖：回退态给的是**下界**，把它说成精确值等于告诉用户「充这么多就够」，
 *    而实际还要加上提示词那一段，充完照样发不出去。
 */
function ShortfallNotice({ shortfall }: { shortfall: ShortfallView }) {
  const { required, exact, available, shortfall: gap } = shortfall;
  return (
    <div className="mb-4 rounded-field border border-line-gold bg-glass-fill px-3 py-2.5">
      <p className="text-[13px] font-medium text-ink">{copy.aibrain.insufficientTitle}</p>
      <p className="mt-1.5 text-[12.5px] leading-relaxed text-error-fg">{copy.aibrain.insufficientReserveNote}</p>
      <ul className="mt-2 space-y-0.5 text-[12.5px] tabular-nums text-ink-soft">
        <li>
          {exact
            ? copy.aibrain.insufficientRequired(formatCredits(required))
            : copy.aibrain.insufficientMinRequired(formatCredits(required))}
        </li>
        {available !== undefined && <li>{copy.aibrain.insufficientAvailable(formatCredits(available))}</li>}
        {gap !== undefined && (
          <li className="text-ink">
            {exact
              ? copy.aibrain.insufficientShortfallExact(formatCredits(gap))
              : copy.aibrain.insufficientShortfall(formatCredits(gap))}
          </li>
        )}
      </ul>
      {/* 回退态特有：余额 ≥ 下界却仍被拒 → 缺口在提示词那一段，前端算不出，换一句话说清方向。
          精确态不会走到这里（BE 的 shortfall 必 > 0）。 */}
      {!exact && available !== undefined && gap === undefined && (
        <p className="mt-2 text-[12px] leading-relaxed text-ink-faint">{copy.aibrain.insufficientContextHint}</p>
      )}
    </div>
  );
}

/**
 * **欠费**说明块（402 `AIBRAIN_OUTSTANDING_BALANCE`，PR #239 `e2bc2c02` 新增）。
 *
 * 🔴 与 `ShortfallNotice` **刻意分成两个组件**，因为话术是相反的：
 *    那边说「这笔钱只是临时锁住、结束会退回」，这边**绝不能出现「会退回」** —— 用户上一次的对话
 *    已经答完并交付了，实际用量超出当时的预留，差额是**真花掉的钱**，现在记成欠款。
 *    把两者合并成一个带 if 的组件，迟早会有人把「临时预留」那句话漏给欠费用户看。
 */
function OutstandingNotice({ outstanding }: { outstanding?: OutstandingView }) {
  return (
    <div className="mb-4 rounded-field border border-line-gold bg-glass-fill px-3 py-2.5">
      <p className="text-[13px] font-medium text-ink">{copy.aibrain.outstandingTitle}</p>
      <p className="mt-1.5 text-[12.5px] leading-relaxed text-error-fg">{copy.aibrain.outstandingNote}</p>
      {outstanding ? (
        <ul className="mt-2 space-y-0.5 text-[12.5px] tabular-nums text-ink-soft">
          <li className="text-ink">{copy.aibrain.outstandingAmount(formatCredits(outstanding.outstanding))}</li>
          {outstanding.available !== undefined && (
            <li>{copy.aibrain.outstandingBalance(formatCredits(outstanding.available))}</li>
          )}
        </ul>
      ) : (
        // detail 与钱包都拿不到数 → 只给定性说明，**不编一个数字出来**。
        <p className="mt-2 text-[12px] leading-relaxed text-ink-faint">{copy.aibrain.outstandingUnknown}</p>
      )}
    </div>
  );
}

/** 402 弹开充值窗时携带的说明——**判别式联合**，逼调用方明确说出是哪一种情形。 */
export type RechargeReason =
  | { kind: "insufficient"; shortfall: ShortfallView }
  | { kind: "outstanding"; outstanding?: OutstandingView };

export function RechargeDialog({
  open,
  onOpenChange,
  reason
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 由 402 / 预检拦截弹开时传入；用户主动点「充值」时不传（那时没有缺口可言，别凭空吓人）。 */
  reason?: RechargeReason;
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

        {reason?.kind === "insufficient" && <ShortfallNotice shortfall={reason.shortfall} />}
        {reason?.kind === "outstanding" && <OutstandingNotice outstanding={reason.outstanding} />}

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
