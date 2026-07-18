"use client";

import type { ScriptLengthTier } from "@/lib/api/types";
import { SelectableOption } from "@/components/ui/selectable-option";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

// 文案字数档位（ECOM-VIDEO-OPTIMIZE-UI-0001 契约 §4.5）：短/中/长三段单选，随「AI生成文案」传 length_tier。
const TIERS: { value: ScriptLengthTier; label: string }[] = [
  { value: "short", label: copy.workbench.scriptLengthShort },
  { value: "medium", label: copy.workbench.scriptLengthMedium },
  { value: "long", label: copy.workbench.scriptLengthLong }
];

/**
 * 文案长度档位选择器 —— 短/中/长三段单选（默认中）。镜像 ResolutionPicker 的三档 SelectableOption 布局，
 * 与工作台其余分段控件视觉/交互一致。受控组件，纯 props；选中值作 scripts/generate 的 length_tier。
 */
export function ScriptLengthPicker({
  value,
  onChange
}: {
  value: ScriptLengthTier;
  onChange: (tier: ScriptLengthTier) => void;
}) {
  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{copy.workbench.scriptLengthLabel}</legend>
      <div className="grid grid-cols-3 gap-2">
        {TIERS.map((tier) => (
          <SelectableOption
            key={tier.value}
            selected={value === tier.value}
            onSelect={() => onChange(tier.value)}
            className="justify-center"
          >
            {tier.label}
          </SelectableOption>
        ))}
      </div>
      <p className="mt-2 text-[12px] text-ink-faint">{copy.workbench.scriptLengthHint}</p>
    </fieldset>
  );
}
