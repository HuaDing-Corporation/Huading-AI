"use client";

import { cn } from "@/lib/utils";

export interface SwitchProps {
  checked: boolean;
  onCheckedChange: (value: boolean) => void;
  id?: string;
  /** 无障碍名（当无关联 <label> 时提供）。 */
  ariaLabel?: string;
  /** aria-describedby 关联提示文案 id。 */
  ariaDescribedby?: string;
  disabled?: boolean;
}

/**
 * 设计系统开关（LABEL-TOGGLE-UI-0001）。role=switch + aria-checked，键盘可达（button，
 * Enter/Space 原生触发 onClick），focus-visible 金色描边。开=淡金轨+金滑块，关=玻璃轨+浅墨滑块，
 * 贴暖香槟鎏金 token。受控组件（父持状态）。
 */
export function Switch({ checked, onCheckedChange, id, ariaLabel, ariaDescribedby, disabled }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      id={id}
      aria-checked={checked}
      aria-label={ariaLabel}
      aria-describedby={ariaDescribedby}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-[22px] w-[38px] flex-none items-center rounded-pill border outline-none transition-colors focus-visible:shadow-focus-gold disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "border-line-sel bg-chip-sel" : "border-line-gold bg-glass-fill"
      )}
    >
      <span
        className={cn(
          "inline-block h-[16px] w-[16px] rounded-full transition-transform",
          checked ? "translate-x-[18px] bg-gold-deep" : "translate-x-[3px] bg-ink-faint"
        )}
      />
    </button>
  );
}
