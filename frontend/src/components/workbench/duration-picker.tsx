"use client";

import { useState } from "react";

import { SelectableOption } from "@/components/ui/selectable-option";
import { copy } from "@/lib/copy";

// 电商带货时长档（ECOM-FIXES-0001 ①）：去掉 45/60，保留 10/15/30 + 自定义。DurationPicker 为电商专属
// （单条 ecom-video-form + 批量 ecom_table common-params）；视频生成用独立 VIDEO_GEN_DURATIONS，不受此影响。
export const DURATION_PRESETS = [10, 15, 30] as const;
export const DURATION_MIN = 5;
export const DURATION_MAX = 120;

/**
 * 目标时长是否合法：**整数** 且在 [5,120]。ECOM-VIDEO-SCENE-DURATION-FIX-UI-0001 · FIX1（CB P1）：真 BE
 * duration_sec 是 int（ScenePromptRequest / VideoGenerateRequest 皆然），拒绝 5.5/5.4 等小数——此前用
 * Number.isFinite 认小数合法、mock 又四舍五入放行 = 假绿，线上用户在「自定义」填 5.5 会真的 422。改用
 * Number.isInteger 从源头拦住（同时守住 scene-prompt 与 视频提交 两条发送路径）。
 */
export function isValidDuration(sec: number): boolean {
  return Number.isInteger(sec) && sec >= DURATION_MIN && sec <= DURATION_MAX;
}

export interface DurationPickerProps {
  value: number;
  onChange: (sec: number) => void;
  /** Legend override — ecom notes this duration drives 文案/视频/字幕 together. */
  label?: string;
}

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * Target video duration for 电商带货 i2v — preset gears (10/15/30s) plus a
 * custom number input (5–120s, aligned with the backend clamp). Pure props;
 * reuses SelectableOption for the gear chips so the selected surface matches the
 * rest of the workbench. The custom input keeps its own text so it stays editable
 * regardless of whether the parent echoes `value` back.
 */
export function DurationPicker({ value, onChange, label }: DurationPickerProps) {
  const isPreset = (DURATION_PRESETS as readonly number[]).includes(value);
  const [custom, setCustom] = useState(!isPreset);
  const [customText, setCustomText] = useState(isPreset ? "" : String(value));

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

  const showError = custom && !isValidDuration(Number(customText));

  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{label ?? copy.workbench.durationLabel}</legend>

      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
        {DURATION_PRESETS.map((sec) => (
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
            min={DURATION_MIN}
            max={DURATION_MAX}
            step={1}
            value={customText}
            onChange={(e) => onCustomInput(e.target.value)}
            aria-label={copy.workbench.durationCustomLabel}
            aria-invalid={showError}
            placeholder={copy.workbench.durationCustomPlaceholder}
            className={`w-full rounded-field border bg-glass-fill px-4 py-2.5 text-sm text-ink outline-none transition-shadow placeholder:text-ink-faint focus:border-line-sel focus:shadow-focus-gold ${
              showError ? "border-error-fg" : "border-line-gold"
            }`}
          />
          {showError && (
            <p role="alert" className="mt-1.5 text-[12.5px] text-error-fg">
              {copy.workbench.durationRange}
            </p>
          )}
        </div>
      )}

      <p className="mt-2 text-[12px] text-ink-faint">{copy.workbench.durationHint}</p>
    </fieldset>
  );
}
