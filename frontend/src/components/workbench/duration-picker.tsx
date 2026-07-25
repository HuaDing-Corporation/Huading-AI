"use client";

import { useEffect, useRef, useState } from "react";

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
  /** 本组件自己最后一次 onChange 出去的值 —— 用来把「用户在操作」与「外部推值」区分开（见下方 effect）。 */
  const lastEmitted = useRef(value);

  /**
   * **外部**推来的受控值 → 把「档位形态」同步过去（REVERSE-DEEP-UI-0001 发现，Code Review 补全成双向）。
   *
   * 原因：`custom` 只在 mount 时由 `useState(!isPreset)` 决定一次。面板「挂载后常驻」，而反推带入是
   * **挂载之后**才把时长推进来的 —— 旧行为下：
   *   - 推来非预设值（如 18s）：value=18 但 custom 仍 false → 没有档位被选中、也不显示自定义框，
   *     **值进了 state 却在界面上看不见**；
   *   - 推来预设值（如 clamp 后的 15s，而 15 正是 video_gen 的预设档）而用户此前手输过自定义 7：
   *     custom 仍 true、框里还是 7，**界面显示 7、实际会提交 15** —— D8 明令禁止的「静默改数」就发生在这里。
   * 故两个方向都要同步。
   *
   * 🔴 只对「不是自己发出去的值」动形态：用 lastEmitted 记下本组件每次 onChange 的值；相等 = 回声（用户正在
   *    输入/点档），此时**绝不**改形态，否则用户在自定义框里敲到 "15" 会被当场踢回预设档。
   *    value 为 NaN（自定义框清空/非数字）同样直接返回，避免把 "NaN" 写回输入框。
   */
  useEffect(() => {
    if (value === lastEmitted.current) return; // 自己发出去的回声 → 不动形态
    lastEmitted.current = value;
    if (Number.isNaN(value)) return;
    setCustom(!isPreset);
    setCustomText(isPreset ? "" : String(value));
  }, [isPreset, value]);

  /** 所有本组件发起的值变更都经这里 —— 记账后再上报，effect 据此识别「回声」不去动形态。 */
  const emit = (sec: number) => {
    lastEmitted.current = sec;
    onChange(sec);
  };

  const selectPreset = (sec: number) => {
    setCustom(false);
    emit(sec);
  };

  const enterCustom = () => {
    setCustom(true);
    const seed = customText || String(value);
    setCustomText(seed);
    emit(Number(seed));
  };

  const onCustomInput = (raw: string) => {
    setCustomText(raw);
    emit(Number(raw)); // NaN when empty/non-numeric → parent gates submit + error shows
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
