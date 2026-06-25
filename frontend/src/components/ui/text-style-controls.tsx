"use client";

import type { ReactNode } from "react";

import type { SubtitlePosition } from "@/lib/api/types";
import { SelectableOption } from "@/components/ui/selectable-option";
import { copy } from "@/lib/copy";

/** 顶部/居中/底部 位置选项（字幕样式区与封面标题共用，单一来源）。 */
export const SUBTITLE_POSITIONS: { id: SubtitlePosition; label: string }[] = [
  { id: "top", label: copy.workbench.subtitlePositionTop },
  { id: "center", label: copy.workbench.subtitlePositionCenter },
  { id: "bottom", label: copy.workbench.subtitlePositionBottom }
];

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

export interface TextStyleControlsProps {
  idPrefix: string; // 字段 id/aria 前缀（subtitle / cover-title），同页两组互不冲突
  fontSizeLabel: string;
  colorLabel: string;
  positionLabel: string;
  fontMin: number;
  fontMax: number;
  fontSize: number;
  onFontSizeChange: (n: number) => void;
  fontError: boolean; // 驱动字号 input 的 aria-invalid + 边框
  fontErrorMessage?: ReactNode; // 字号越界文案，渲染于字号/颜色与位置之间（父级控制文案与间距）
  color: string; // #RRGGBB（用户自选，数据驱动 inline）
  onColorChange: (hex: string) => void;
  position: SubtitlePosition;
  onPositionChange: (p: SubtitlePosition) => void;
}

/**
 * 「字号(number, clamp) + 颜色(color input) + 位置(3 选 1)」一组控件 —— 字幕样式区
 * 与封面标题两处结构相同，参数化 min/max + 各字段 value/onChange 后共用，消除复制粘贴。
 * 父级各自维护状态语义（picker patch SubtitleStyle / cover 独立 setState）并渲染错误文案。
 */
export function TextStyleControls({
  idPrefix,
  fontSizeLabel,
  colorLabel,
  positionLabel,
  fontMin,
  fontMax,
  fontSize,
  onFontSizeChange,
  fontError,
  fontErrorMessage,
  color,
  onColorChange,
  position,
  onPositionChange
}: TextStyleControlsProps) {
  const fontId = `${idPrefix}-font-size`;
  const colorId = `${idPrefix}-color`;
  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label htmlFor={fontId} className={labelClass}>
            {fontSizeLabel}
          </label>
          <input
            id={fontId}
            type="number"
            inputMode="numeric"
            min={fontMin}
            max={fontMax}
            step={1}
            value={String(fontSize)}
            onChange={(e) => onFontSizeChange(Number(e.target.value))}
            aria-invalid={fontError}
            className={`w-full rounded-field border bg-glass-fill px-3 py-2 text-sm text-ink outline-none transition-shadow focus:border-line-sel focus:shadow-focus-gold ${
              fontError ? "border-error-fg" : "border-line-gold"
            }`}
          />
        </div>
        <div>
          <label htmlFor={colorId} className={labelClass}>
            {colorLabel}
          </label>
          <input
            id={colorId}
            type="color"
            value={color}
            onChange={(e) => onColorChange(e.target.value.toUpperCase())}
            aria-label={colorLabel}
            className="h-[38px] w-full cursor-pointer rounded-field border border-line-gold bg-glass-fill px-1"
          />
        </div>
      </div>

      {fontErrorMessage}

      <div>
        <label className={labelClass}>{positionLabel}</label>
        <div className="grid grid-cols-3 gap-2">
          {SUBTITLE_POSITIONS.map((p) => (
            <SelectableOption
              key={p.id}
              selected={position === p.id}
              onSelect={() => onPositionChange(p.id)}
              className="justify-center"
            >
              {p.label}
            </SelectableOption>
          ))}
        </div>
      </div>
    </>
  );
}
