"use client";

// 华鼎AI智脑 · 智能强度选择器（AIBRAIN-UI-0001）。低/中/高 + **每档旁标消耗倍率**（D3，让用户有预期）。
// a11y：role=radiogroup / radio + aria-checked + 键盘（左右方向键切换），选中不靠颜色单一（加勾/描边）。
//
// 🔴 PRICING-UI-0001 §二：「约 N 积分/次」不再是写死的 6/15/30，而是由 `typicalCredits()` 从费率推导；
//    并在选择器下方常驻一行**口径说明**（真实费率 + 这个「约」是怎么估的）。
//    口径行为什么不做成 hover tooltip：触屏上 hover 不可达，等于对一半用户没有说明——
//    而「不许出现无说明的裸价格整数」这条要求，对触屏用户同样成立。

import type { KeyboardEvent } from "react";

import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import {
  TIER_ORDER,
  TIERS,
  TYPICAL_COMPLETION_TOKENS,
  TYPICAL_PROMPT_TOKENS,
  formatRate,
  typicalCredits,
  type IntensityTier
} from "@/lib/aibrain/types";

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
    <div className="flex flex-col gap-1">
      <div
        role="radiogroup"
        aria-label={copy.aibrain.intensityLabel}
        onKeyDown={onKeyDown}
        className="inline-flex w-fit items-center gap-1 rounded-field border border-line-gold bg-glass-soft p-1"
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
                "flex flex-col items-center rounded-chip px-3 py-1 outline-none transition-colors focus-visible:shadow-focus-gold disabled:opacity-50",
                selected ? "bg-chip-sel text-ink shadow-mark" : "text-ink-soft hover:bg-glass-hover"
              )}
            >
              <span className="text-[13px] font-medium leading-tight">{meta.label}</span>
              <span className="text-[10.5px] leading-tight text-ink-faint tabular-nums">
                {copy.aibrain.intensityCost(typicalCredits(tier))}
              </span>
            </button>
          );
        })}
      </div>
      {/* 🔴 口径行：跟随当前档位给出真实费率。没有它，上面那三个「约 N 积分」又是三个裸整数。 */}
      <p className="max-w-[52ch] text-[10.5px] leading-snug text-ink-faint">
        {copy.aibrain.intensityRateHint(
          formatRate(current.rate.inputPer1k),
          formatRate(current.rate.outputPer1k),
          TYPICAL_PROMPT_TOKENS,
          TYPICAL_COMPLETION_TOKENS
        )}
      </p>
    </div>
  );
}
