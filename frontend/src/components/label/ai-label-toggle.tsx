"use client";

import { useId } from "react";

import { Switch } from "@/components/ui/switch";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

export interface AiLabelToggleProps {
  checked: boolean;
  onChange: (value: boolean) => void;
  className?: string;
}

/**
 * 「AI 生成标识」开关（LABEL-TOGGLE-UI-0001）—— 六大工作台生成面板统一复用。默认关；开=提交
 * apply_visible_label:true。开关旁一行提示「关闭后…请自行完成 AI 内容声明」（不弹窗、不阻断）。
 * 受控组件（父持状态并用 useLabelTogglePreference 记忆）。移动端：标签+开关一行、提示换行不错位。
 */
export function AiLabelToggle({ checked, onChange, className }: AiLabelToggleProps) {
  const hintId = useId();
  return (
    <div className={cn("mb-[15px]", className)}>
      <div className="flex items-center justify-between gap-3">
        <span className="text-[12.5px] tracking-[.5px] text-ink-soft">{copy.label.toggleLabel}</span>
        <Switch checked={checked} onCheckedChange={onChange} ariaLabel={copy.label.toggleLabel} ariaDescribedby={hintId} />
      </div>
      <p id={hintId} className="mt-1.5 text-[12px] leading-relaxed text-ink-faint">
        {copy.label.toggleHint}
      </p>
    </div>
  );
}
