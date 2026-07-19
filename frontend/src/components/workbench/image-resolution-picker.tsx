"use client";

import { SelectableOption } from "@/components/ui/selectable-option";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

// 图片清晰度档位（IMAGE-GEN-OPTIMIZE-UI-0001 §3之二）：1K/2K/4K，默认 1K（与现状一致）。与「画面比例」并列——
// 比例定形状、档位定大小。⚠️ 是「更大尺寸」不是「变清晰」；界面选择是硬条件、总随请求传（值 image_resolution）。
export type ImageResolutionTier = "1k" | "2k" | "4k";
export const IMAGE_RESOLUTION_TIERS: ImageResolutionTier[] = ["1k", "2k", "4k"];
export const DEFAULT_IMAGE_RESOLUTION: ImageResolutionTier = "1k";

export function ImageResolutionPicker({
  value,
  onChange
}: {
  value: ImageResolutionTier;
  onChange: (tier: ImageResolutionTier) => void;
}) {
  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{copy.workbench.imageResolutionLabel}</legend>
      <div className="grid grid-cols-3 gap-2">
        {IMAGE_RESOLUTION_TIERS.map((t) => (
          <SelectableOption key={t} selected={value === t} onSelect={() => onChange(t)} className="justify-center">
            {t.toUpperCase()}
          </SelectableOption>
        ))}
      </div>
      <p className="mt-2 text-[12px] text-ink-faint">{copy.workbench.imageResolutionHint}</p>
    </fieldset>
  );
}
