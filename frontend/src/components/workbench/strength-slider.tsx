"use client";

import { useId } from "react";

import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

export interface StrengthSliderProps {
  label: string;
  /** 诚实提示（软性倾向、非精确控制）。 */
  hint?: string;
  enabled: boolean;
  value: number; // 10..100 步长 10
  onEnabledChange: (enabled: boolean) => void;
  onValueChange: (value: number) => void;
  id?: string;
}

/**
 * 强度滑块行（IMAGE-GEN-OPTIMIZE-UI-0001）—— 开关(默认关) + 暖金 Slider + 当前值%。**关闭时 slider disabled 且不参与提交**
 * （父级据 enabled 决定是否放进请求体，未开启的强度必须不出现）。文案诚实：这些强度底层是编码进提示词的**软性倾向**、
 * 非 provider 原生精确参数，措辞用「倾向/更偏向/尽量」（见 copy）。受控组件，纯 props。
 */
export function StrengthSlider({ label, hint, enabled, value, onEnabledChange, onValueChange, id }: StrengthSliderProps) {
  const uid = useId();
  const sliderId = id ?? uid;
  const hintId = `${sliderId}-hint`;
  return (
    <div className="mb-3">
      <div className="flex items-center justify-between gap-3">
        <label htmlFor={sliderId} className="text-[12.5px] tracking-[.5px] text-ink-soft">
          {label}
        </label>
        <div className="flex items-center gap-2.5">
          <span
            className={cn(
              "min-w-[3rem] text-right text-[12.5px] tabular-nums",
              enabled ? "text-gold-deep" : "text-ink-faint"
            )}
          >
            {enabled ? `${value}%` : copy.workbench.strengthOff}
          </span>
          <Switch checked={enabled} onCheckedChange={onEnabledChange} ariaLabel={`${label}${copy.workbench.strengthToggleSuffix}`} />
        </div>
      </div>

      <Slider
        id={sliderId}
        value={value}
        onChange={onValueChange}
        min={10}
        max={100}
        step={10}
        disabled={!enabled}
        ariaLabel={label}
        // 关态诚实播报：disabled 时 SR 说「未开启」而非「50%」（该值不入提交体，Code Review 低危矫正）
        valueText={enabled ? `${value}%` : copy.workbench.strengthOff}
        ariaDescribedby={hint ? hintId : undefined}
        className="mt-2"
      />

      {hint && (
        <p id={hintId} className="mt-1 text-[12px] text-ink-faint">
          {hint}
        </p>
      )}
    </div>
  );
}
