"use client";

import { cn } from "@/lib/utils";

export interface SliderProps {
  id?: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  /** 可及名（screen reader）——用于 aria-label。 */
  ariaLabel?: string;
  /** 当前值的可读播报（如「60%」）——用于 aria-valuetext，比裸数字更清楚。 */
  valueText?: string;
  ariaDescribedby?: string;
  className?: string;
}

/**
 * 暖金强度滑块（IMAGE-GEN-OPTIMIZE-UI-0001，新建 ui/slider.tsx）。视觉参考 Claude Code 的 Effort 滑块
 * （渐变条 + 档位刻度），但用本站暖金配色（--grad-gold / --gold），不引入紫色。
 *
 * a11y：底层是原生 `<input type="range">` —— 天然键盘可操作（方向键按 step 逐档、Home/End 到端点）、
 * role=slider + aria-valuemin/max/now 由浏览器提供；额外给 aria-label（可及名）与 aria-valuetext（如「60%」，
 * 让 SR 播报百分比而非裸数字）。disabled 时不可聚焦、不参与 tab。填充渐变与刻度纯装饰（aria-hidden）。
 */
export function Slider({
  id,
  value,
  onChange,
  min = 0,
  max = 100,
  step = 1,
  disabled = false,
  ariaLabel,
  valueText,
  ariaDescribedby,
  className
}: SliderProps) {
  const pct = max > min ? ((value - min) / (max - min)) * 100 : 0;
  const stepsCount = Math.round((max - min) / step) + 1; // 含两端的档位数（10..100 步10 → 10 档）

  return (
    <div className={cn("relative", className)}>
      <div className="relative h-5">
        {/* 底轨（未填充） */}
        <div className="pointer-events-none absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-line-gold/40" aria-hidden />
        {/* 已填充：暖金渐变，宽度=当前值百分比 */}
        <div
          className="pointer-events-none absolute left-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full transition-[width] duration-150"
          style={{ width: `${pct}%`, background: "var(--grad-gold)", opacity: disabled ? 0.4 : 1 }}
          aria-hidden
        />
        <input
          id={id}
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(Number(e.target.value))}
          aria-label={ariaLabel}
          aria-valuetext={valueText}
          aria-describedby={ariaDescribedby}
          className={cn(
            "relative z-10 h-5 w-full cursor-pointer appearance-none bg-transparent outline-none",
            "disabled:cursor-not-allowed",
            // 拇指（webkit）：暖金圆 + 白描边 + 聚焦金环
            "[&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-white [&::-webkit-slider-thumb]:bg-gold",
            "focus-visible:[&::-webkit-slider-thumb]:shadow-focus-gold disabled:[&::-webkit-slider-thumb]:bg-ink-faint",
            // 拇指（firefox）：outline-none 抹掉了原生焦点环，故 moz 拇指也要补 focus-visible 金环，否则 Firefox 键盘聚焦无指示（Code Review medium · WCAG 2.4.7）
            "[&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-white [&::-moz-range-thumb]:bg-gold",
            "focus-visible:[&::-moz-range-thumb]:shadow-focus-gold disabled:[&::-moz-range-thumb]:bg-ink-faint"
          )}
        />
      </div>
      {/* 档位刻度（纯装饰）：已达档位=暖金深，未达=细线金 */}
      <div className="mt-1 flex justify-between px-0.5" aria-hidden>
        {Array.from({ length: stepsCount }, (_, i) => {
          const tickVal = min + i * step;
          const reached = !disabled && tickVal <= value;
          return <span key={tickVal} className={cn("h-1.5 w-px rounded-full", reached ? "bg-gold-deep" : "bg-line-gold")} />;
        })}
      </div>
    </div>
  );
}
