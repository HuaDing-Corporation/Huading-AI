"use client";

// 华鼎AI智脑 · 智能强度选择器（AIBRAIN-UI-0001）。低/中/高 + **每档旁标消耗倍率**（D3，让用户有预期）。
// a11y：role=radiogroup / radio + aria-checked + 键盘（左右方向键切换），选中不靠颜色单一（加勾/描边）。

import type { KeyboardEvent } from "react";

import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import { TIER_ORDER, TIERS, type IntensityTier } from "@/lib/aibrain/types";

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

  return (
    <div
      role="radiogroup"
      aria-label={copy.aibrain.intensityLabel}
      onKeyDown={onKeyDown}
      className="inline-flex items-center gap-1 rounded-field border border-line-gold bg-glass-soft p-1"
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
            aria-label={copy.aibrain.intensityAria(meta.label, meta.typical)}
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
              {copy.aibrain.intensityCost(meta.typical)}
            </span>
          </button>
        );
      })}
    </div>
  );
}
