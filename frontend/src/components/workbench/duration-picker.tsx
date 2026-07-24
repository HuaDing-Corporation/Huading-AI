"use client";

import { useEffect, useState } from "react";

import { SelectableOption } from "@/components/ui/selectable-option";
import { copy } from "@/lib/copy";

// 电商带货时长档（ECOM-FIXES-0001 ①）：去掉 45/60，保留 10/15/30 + 自定义。DurationPicker 为电商专属
// （单条 ecom-video-form + 批量 ecom_table common-params）；视频生成用独立 VIDEO_GEN_DURATIONS，不受此影响。
export const DURATION_PRESETS = [10, 15, 30] as const;
export const DURATION_MIN = 5;
export const DURATION_MAX = 120;

/**
 * 目标时长是否合法：**整数** 且在 [min,max]（默认 [5,120]=电商带货）。ECOM-VIDEO-SCENE-DURATION-FIX-UI-0001 · FIX1（CB P1）：
 * 真 BE duration_sec 是 int（ScenePromptRequest / VideoGenerateRequest 皆然），拒绝 5.5/5.4 等小数——此前用
 * Number.isFinite 认小数合法、mock 又四舍五入放行 = 假绿，线上用户在「自定义」填 5.5 会真的 422。改用
 * Number.isInteger 从源头拦住（同时守住 scene-prompt 与 视频提交 两条发送路径）。
 * VIDEO-GEN-PARAMS-UI-0001：区间改 per-call（视频生成传 [4,15]=provider 硬范围）；默认不变，电商零回归。
 */
export function isValidDuration(sec: number, min: number = DURATION_MIN, max: number = DURATION_MAX): boolean {
  return Number.isInteger(sec) && sec >= min && sec <= max;
}

export interface DurationPickerProps {
  value: number;
  onChange: (sec: number) => void;
  /** Legend override — ecom notes this duration drives 文案/视频/字幕 together. */
  label?: string;
  /** 预设档 / 自定义区间（默认电商 10/15/30 + [5,120]；视频生成传 5/10/15 + [4,15]）。per-call，别回归电商。 */
  presets?: readonly number[];
  min?: number;
  max?: number;
}

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * Target video duration for 电商带货 i2v — preset gears (10/15/30s) plus a
 * custom number input (5–120s, aligned with the backend clamp). Pure props;
 * reuses SelectableOption for the gear chips so the selected surface matches the
 * rest of the workbench. The custom input keeps its own text so it stays editable
 * regardless of whether the parent echoes `value` back.
 */
export function DurationPicker({
  value,
  onChange,
  label,
  presets = DURATION_PRESETS,
  min = DURATION_MIN,
  max = DURATION_MAX
}: DurationPickerProps) {
  const isPreset = (presets as readonly number[]).includes(value);
  const [custom, setCustom] = useState(!isPreset);
  const [customText, setCustomText] = useState(isPreset ? "" : String(value));

  /**
   * 受控值变成**非预设档**时切到自定义档并回显（REVERSE-DEEP-UI-0001 发现）。
   * 原因：`custom` 只在 mount 时由 `useState(!isPreset)` 决定一次。面板「挂载后常驻」+ 反推带入是
   * **挂载之后**才把时长推进来的（如原视频 18s）——旧行为下 value=18 但 custom 仍为 false，
   * 于是既没有档位被选中、也不显示自定义输入框：**值进了 state 却在界面上看不见**（用户无从确认带入了什么，
   * 也改不了）。这与 D3-①「逐字段直落到对应控件」相悖，故在此把「值」与「档位形态」对齐。
   * 不覆盖用户正在输入的文本：value 与当前文本数值一致时原样保留（"07" 这类写法不被改写）；
   * value 为 NaN（自定义框被清空/填了非数字）时直接返回，避免把 "NaN" 写回输入框。
   */
  useEffect(() => {
    if (isPreset || Number.isNaN(value)) return;
    setCustom(true);
    setCustomText((prev) => (Number(prev) === value ? prev : String(value)));
  }, [isPreset, value]);

  const selectPreset = (sec: number) => {
    setCustom(false);
    onChange(sec);
  };

  const enterCustom = () => {
    setCustom(true);
    const seed = customText || String(value);
    setCustomText(seed);
    onChange(Number(seed));
  };

  const onCustomInput = (raw: string) => {
    setCustomText(raw);
    onChange(Number(raw)); // NaN when empty/non-numeric → parent gates submit + error shows
  };

  const showError = custom && !isValidDuration(Number(customText), min, max);

  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{label ?? copy.workbench.durationLabel}</legend>

      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
        {presets.map((sec) => (
          <SelectableOption
            key={sec}
            selected={!custom && value === sec}
            onSelect={() => selectPreset(sec)}
            className="justify-center"
          >
            {copy.workbench.durationSeconds(sec)}
          </SelectableOption>
        ))}
        <SelectableOption selected={custom} onSelect={enterCustom} className="justify-center">
          {copy.workbench.durationCustom}
        </SelectableOption>
      </div>

      {custom && (
        <div className="mt-2.5">
          <input
            type="number"
            inputMode="numeric"
            min={min}
            max={max}
            step={1}
            value={customText}
            onChange={(e) => onCustomInput(e.target.value)}
            aria-label={copy.workbench.durationCustomLabel}
            aria-invalid={showError}
            placeholder={copy.workbench.durationCustomPlaceholder(min, max)}
            className={`w-full rounded-field border bg-glass-fill px-4 py-2.5 text-sm text-ink outline-none transition-shadow placeholder:text-ink-faint focus:border-line-sel focus:shadow-focus-gold ${
              showError ? "border-error-fg" : "border-line-gold"
            }`}
          />
          {showError && (
            <p role="alert" className="mt-1.5 text-[12.5px] text-error-fg">
              {copy.workbench.durationRange(min, max)}
            </p>
          )}
        </div>
      )}

      <p className="mt-2 text-[12px] text-ink-faint">{copy.workbench.durationHint}</p>
    </fieldset>
  );
}
