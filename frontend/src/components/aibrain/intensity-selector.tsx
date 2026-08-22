"use client";

// 华鼎AI智脑 · 智能强度选择器（AIBRAIN-UI-0001）。低/中/高 + **每档旁标消耗倍率**（D3，让用户有预期）。
// a11y：role=radiogroup / radio + aria-checked + 键盘（左右方向键切换），选中不靠颜色单一（加勾/描边）。
//
// 🔴 PRICING-UI-0001 §二：「约 N 积分/次」不再是写死的 6/15/30，而是由 `typicalCredits()` 从费率推导；
//    并在选择器下方常驻一行**口径说明**（真实费率 + 这个「约」是怎么估的）。
//    口径行为什么不做成 hover tooltip：触屏上 hover 不可达，等于对一半用户没有说明——
//    而「不许出现无说明的裸价格整数」这条要求，对触屏用户同样成立。

// 🔴 PRICING-UI-0002：费率有了**两个区间**（≤272K / >272K）。常驻行仍只写低区间——那是绝大多数
//    会话的口径，一行塞四个数没人看；高区间用 <details> 折叠。但**必须点明还有另一档**，否则用户
//    会以为只有一个费率，跑进长上下文后就是「显示 1.12、实扣 2.24」。
//    用原生 <details> 而不是自造展开：键盘可达、屏幕阅读器有语义、无 JS 状态，触屏也能点开
//    （与"口径行不做 hover tooltip"同一条理由）。

import type { KeyboardEvent } from "react";

import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import {
  PROMPT_RATE_TIER_THRESHOLD_TOKENS,
  TIER_ORDER,
  TIERS,
  TYPICAL_COMPLETION_TOKENS,
  TYPICAL_PROMPT_TOKENS,
  formatRate,
  typicalCredits,
  type IntensityTier
} from "@/lib/aibrain/types";

/** 阈值以「万 token」示人：272,000 说成「27.2 万」比一串零可读得多，且与中文习惯一致。 */
const THRESHOLD_IN_WAN = PROMPT_RATE_TIER_THRESHOLD_TOKENS / 10_000;

export function IntensitySelector({
  value,
  onChange,
  disabled
}: {
  value: IntensityTier;
  onChange: (tier: IntensityTier) => void;
  disabled?: boolean;
}) {
  const onKeyDown = (e: KeyboardEvent) => {
    if (disabled) return;
    const idx = TIER_ORDER.indexOf(value);
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      onChange(TIER_ORDER[(idx + 1) % TIER_ORDER.length]);
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      onChange(TIER_ORDER[(idx - 1 + TIER_ORDER.length) % TIER_ORDER.length]);
    }
  };

  const current = TIERS[value];

  return (
    <div className="flex w-full min-w-0 flex-col gap-1 sm:w-auto">
      <div
        role="radiogroup"
        aria-label={copy.aibrain.intensityLabel}
        onKeyDown={onKeyDown}
        className="grid w-full grid-cols-3 items-center gap-1 rounded-field border border-line-gold bg-glass-soft p-1 sm:inline-flex sm:w-fit"
      >
        {TIER_ORDER.map((tier) => {
          const meta = TIERS[tier];
          const selected = tier === value;
          return (
            <button
              key={tier}
              type="button"
              role="radio"
              aria-checked={selected}
              aria-label={copy.aibrain.intensityAria(meta.label, typicalCredits(tier))}
              tabIndex={selected ? 0 : -1}
              disabled={disabled}
              onClick={() => onChange(tier)}
              className={cn(
                "flex min-w-0 flex-col items-center rounded-chip px-2 py-1 outline-none transition-colors focus-visible:shadow-focus-gold disabled:opacity-50 sm:px-3",
                selected ? "bg-chip-sel text-ink shadow-mark" : "text-ink-soft hover:bg-glass-hover"
              )}
            >
              <span className="text-[13px] font-medium leading-tight">{meta.label}</span>
              <span className="whitespace-nowrap text-[10.5px] leading-tight text-ink-faint tabular-nums">
                {copy.aibrain.intensityCost(typicalCredits(tier))}
              </span>
            </button>
          );
        })}
      </div>
      {/* 🔴 口径行：跟随当前档位给出真实费率。没有它，上面那三个「约 N 积分」又是三个裸整数。 */}
      <p className="max-w-[52ch] text-[10.5px] leading-snug text-ink-faint">
        {copy.aibrain.intensityRateHint(
          formatRate(current.rate.standard.inputPer1k),
          formatRate(current.rate.standard.outputPer1k),
          TYPICAL_PROMPT_TOKENS,
          TYPICAL_COMPLETION_TOKENS
        )}
        {/* 🔴 常驻点明「还有一档」——不写这句，上面那行就成了"唯一费率"的暗示。 */}
        <span className="ml-1">{copy.aibrain.intensityRateTierNote(THRESHOLD_IN_WAN)}</span>
      </p>
      {/* 高区间明细：折叠，不占常驻行。原生 <details> → 键盘/读屏/触屏都可达。 */}
      <details className="max-w-[52ch] text-[10.5px] leading-snug text-ink-faint">
        <summary className="cursor-pointer outline-none focus-visible:shadow-focus-gold">
          {copy.aibrain.intensityRateTierToggle}
        </summary>
        <p className="mt-1">
          {copy.aibrain.intensityRateTierDetail(
            THRESHOLD_IN_WAN,
            formatRate(current.rate.extended.inputPer1k),
            formatRate(current.rate.extended.outputPer1k)
          )}
        </p>
      </details>
    </div>
  );
}
