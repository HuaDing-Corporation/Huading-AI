"use client";

import type { SubtitleStyle, SubtitleTemplate } from "@/lib/api/types";
import { SelectableOption } from "@/components/ui/selectable-option";
import { TextStyleControls } from "@/components/ui/text-style-controls";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const FONT_MIN = 16;
const FONT_MAX = 96;
// 烧入像素 → 预览框(140px 宽 9:16)的近似缩放，仅为相对大小直观，非真烧入。
const PREVIEW_FONT_SCALE = 0.34;

/** 字幕样式校验：未选 = 合法(不传)；选了则 font_size 覆盖须在 16..96(与后端 clamp 对齐)。 */
export function isSubtitleStyleValid(style?: SubtitleStyle): boolean {
  if (!style) return true;
  if (style.font_size !== undefined && !(Number.isFinite(style.font_size) && style.font_size >= FONT_MIN && style.font_size <= FONT_MAX)) {
    return false;
  }
  return true;
}

export interface SubtitleStylePickerProps {
  templates: SubtitleTemplate[];
  value: SubtitleStyle | undefined;
  onChange: (style: SubtitleStyle | undefined) => void;
}

/**
 * 字幕样式区(口播表单内，ORAL-PROD-UI-0001) — 纯 props(容器 NewVideoForm 拉 templates
 * 并持有 value)。预设 5 套 + 「跟随默认」(= value undefined，不传 subtitle_style，不回归 0001)；
 * 选预设后可微调字号/颜色/位置(覆盖项，留空继承预设)。CSS 在占位帧上近似预览所选 style(非真烧入)。
 * 用户自选字幕色 = 数据驱动 inline style(合法)；组件框架色全用 Design token。
 */
export function SubtitleStylePicker({ templates, value, onChange }: SubtitleStylePickerProps) {
  const base = value ? templates.find((t) => t.id === value.template_id) : undefined;
  const merged = base
    ? {
        font_family: base.font_family,
        font_size: value?.font_size ?? base.font_size,
        color: value?.color ?? base.color,
        position: value?.position ?? base.position,
        stroke_color: base.stroke_color,
        stroke_width: base.stroke_width,
        background: base.background
      }
    : null;

  const showFontError = !!value && value.font_size !== undefined && !isSubtitleStyleValid(value);
  // 预览字号回退：非法/清空(Number("")=0)时用预设字号，避免预览渲染成 0px 文字消失（Code Review）。
  const previewFontSize = merged ? (isSubtitleStyleValid(value) ? merged.font_size : base?.font_size ?? merged.font_size) : 0;
  const patch = (p: Partial<SubtitleStyle>) => {
    if (value) onChange({ ...value, ...p });
  };

  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{copy.workbench.subtitleStyleLabel}</legend>
      <p className="mb-2 text-[12px] text-ink-faint">{copy.workbench.subtitleStyleHint}</p>

      <div className="grid grid-cols-3 gap-2">
        <SelectableOption selected={!value} onSelect={() => onChange(undefined)} className="justify-center">
          {copy.workbench.subtitleStyleNone}
        </SelectableOption>
        {templates.map((t) => (
          <SelectableOption
            key={t.id}
            selected={value?.template_id === t.id}
            onSelect={() => onChange({ template_id: t.id })}
            className="justify-center"
          >
            {t.name}
          </SelectableOption>
        ))}
      </div>

      {merged && (
        <div className="mt-3 flex flex-col gap-3">
          {/* CSS 近似预览(非真烧入)：token 渐变占位帧 + 用户 style 的字幕文字 */}
          <div
            className="mx-auto flex aspect-[9/16] w-[140px] overflow-hidden rounded-field border border-line-gold bg-gradient-to-b from-ink/80 to-ink-soft/40 px-2"
            style={{
              alignItems: merged.position === "top" ? "flex-start" : merged.position === "center" ? "center" : "flex-end",
              justifyContent: "center"
            }}
          >
            <span
              className="my-2.5 px-1.5 py-0.5 text-center font-medium leading-tight"
              style={{
                fontFamily: merged.font_family,
                fontSize: `${Math.round(previewFontSize * PREVIEW_FONT_SCALE)}px`,
                color: merged.color,
                backgroundColor: merged.background ?? undefined,
                textShadow:
                  merged.stroke_width > 0 && merged.stroke_color
                    ? `0 0 ${merged.stroke_width}px ${merged.stroke_color}, 0 0 ${merged.stroke_width}px ${merged.stroke_color}`
                    : undefined
              }}
            >
              {copy.workbench.subtitlePreviewSample}
            </span>
          </div>

          <TextStyleControls
            idPrefix="subtitle"
            fontSizeLabel={copy.workbench.subtitleFontSizeLabel}
            colorLabel={copy.workbench.subtitleColorLabel}
            positionLabel={copy.workbench.subtitlePositionLabel}
            fontMin={FONT_MIN}
            fontMax={FONT_MAX}
            fontSize={merged.font_size}
            onFontSizeChange={(n) => patch({ font_size: n })}
            fontError={showFontError}
            fontErrorMessage={
              showFontError ? (
                <p role="alert" className="text-[12.5px] text-error-fg">
                  {copy.workbench.subtitleFontSizeRange}
                </p>
              ) : undefined
            }
            color={merged.color}
            onColorChange={(hex) => patch({ color: hex })}
            position={merged.position}
            onPositionChange={(p) => patch({ position: p })}
          />
        </div>
      )}
    </fieldset>
  );
}
