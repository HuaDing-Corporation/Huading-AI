"use client";

import { useState } from "react";

import { SelectableOption } from "@/components/ui/selectable-option";
import { copy } from "@/lib/copy";

// 产品图张数（ECOM-VIDEO-OPTIMIZE-UI-0001 契约 §4.3/req2）：预设 1–5 + 自定义（上限 9，对齐 video-gen picker）。
// 张数 = 产品图上限：选值后 picker 最多传那么多；多图分配到不同分镜（§4.4）。
export const IMAGE_COUNT_PRESETS = [1, 2, 3, 4, 5] as const;
export const IMAGE_COUNT_MIN = 1;
export const IMAGE_COUNT_MAX = 9;

/** 张数是否在契约范围内（整数 1–9）。父级据此卡自定义输入合法性。 */
export function isValidImageCount(n: number): boolean {
  return Number.isInteger(n) && n >= IMAGE_COUNT_MIN && n <= IMAGE_COUNT_MAX;
}

export interface ProductImageCountPickerProps {
  value: number;
  onChange: (count: number) => void;
}

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * 产品图张数选择器 —— 预设档位（1/2/3/4/5）+ 自定义数字输入（1–9）。镜像 DurationPicker：复用
 * SelectableOption 让选中态与工作台其余控件一致；自定义输入保留自身文本，父级回显 value 不影响可编辑性。
 * 受控组件，纯 props。选值作产品图上限（多图 picker 的 max）；切档不删已上传图（由父级明确拦截，不静默丢）。
 */
export function ProductImageCountPicker({ value, onChange }: ProductImageCountPickerProps) {
  const isPreset = (IMAGE_COUNT_PRESETS as readonly number[]).includes(value);
  const [custom, setCustom] = useState(!isPreset);
  const [customText, setCustomText] = useState(isPreset ? "" : String(value));

  const selectPreset = (n: number) => {
    setCustom(false);
    onChange(n);
  };

  const enterCustom = () => {
    setCustom(true);
    const seed = customText || String(value);
    setCustomText(seed);
    onChange(Number(seed));
  };

  const onCustomInput = (raw: string) => {
    setCustomText(raw);
    // Number("")===0、Number("abc")===NaN —— 两者都非法(0<MIN、NaN 非整)，均由父级 isValidImageCount 卡提交 +
    // 本组件 showError 就地提示；父级越限文案已守 isValidImageCount，不会拿 0 报「超过所选 0 张」。
    onChange(Number(raw));
  };

  const showError = custom && !isValidImageCount(Number(customText));

  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{copy.workbench.productImageCountLabel}</legend>

      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        {IMAGE_COUNT_PRESETS.map((n) => (
          <SelectableOption
            key={n}
            selected={!custom && value === n}
            onSelect={() => selectPreset(n)}
            className="justify-center"
          >
            {copy.workbench.productImageCount(n)}
          </SelectableOption>
        ))}
        <SelectableOption selected={custom} onSelect={enterCustom} className="justify-center">
          {copy.workbench.productImageCountCustom}
        </SelectableOption>
      </div>

      {custom && (
        <div className="mt-2.5">
          <input
            type="number"
            inputMode="numeric"
            min={IMAGE_COUNT_MIN}
            max={IMAGE_COUNT_MAX}
            step={1}
            value={customText}
            onChange={(e) => onCustomInput(e.target.value)}
            aria-label={copy.workbench.productImageCountCustomLabel}
            aria-invalid={showError}
            placeholder={copy.workbench.productImageCountPlaceholder}
            className={`w-full rounded-field border bg-glass-fill px-4 py-2.5 text-sm text-ink outline-none transition-shadow placeholder:text-ink-faint focus:border-line-sel focus:shadow-focus-gold ${
              showError ? "border-error-fg" : "border-line-gold"
            }`}
          />
          {showError && (
            <p role="alert" className="mt-1.5 text-[12.5px] text-error-fg">
              {copy.workbench.productImageCountRange}
            </p>
          )}
        </div>
      )}

      <p className="mt-2 text-[12px] text-ink-faint">{copy.workbench.productImageCountHint}</p>
    </fieldset>
  );
}
