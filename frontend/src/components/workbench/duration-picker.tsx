"use client";

import { useState } from "react";

import { SelectableOption } from "@/components/ui/selectable-option";
import { copy } from "@/lib/copy";

export const DURATION_PRESETS = [15, 30, 45, 60] as const;
export const DURATION_MIN = 5;
export const DURATION_MAX = 120;

/** Whether a target duration is within the contract range the backend clamps to. */
export function isValidDuration(sec: number): boolean {
  return Number.isFinite(sec) && sec >= DURATION_MIN && sec <= DURATION_MAX;
}

export interface DurationPickerProps {
  value: number;
  onChange: (sec: number) => void;
}

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * Target video duration for 电商带货 i2v — preset gears (15/30/45/60s) plus a
 * custom number input (5–120s, aligned with the backend clamp). Pure props;
 * reuses SelectableOption for the gear chips so the selected surface matches the
 * rest of the workbench. The custom input keeps its own text so it stays editable
 * regardless of whether the parent echoes `value` back.
 */
export function DurationPicker({ value, onChange }: DurationPickerProps) {
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
      <legend className={labelClass}>{copy.workbench.durationLabel}</legend>

      <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
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
