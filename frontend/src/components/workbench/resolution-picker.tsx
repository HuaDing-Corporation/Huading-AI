"use client";

import type { VideoGenResolution } from "@/lib/api/types";
import { SelectableOption } from "@/components/ui/selectable-option";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const RESOLUTIONS: VideoGenResolution[] = ["480p", "720p", "1080p"];

/**
 * 分辨率选择器（ECOM-RESOLUTION-UI-0001）——自视频生成表单原样抽取的同款三档 480P/720P/1080P
 * fieldset（含「更高分辨率更清晰…」提示），供 视频生成 / 电商带货 复用。受控组件，纯 props。
 */
export function ResolutionPicker({
  value,
  onChange
}: {
  value: VideoGenResolution;
  onChange: (r: VideoGenResolution) => void;
}) {
  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{copy.workbench.vgResolutionLabel}</legend>
      <div className="grid grid-cols-3 gap-2">
        {RESOLUTIONS.map((r) => (
          <SelectableOption key={r} selected={value === r} onSelect={() => onChange(r)} className="justify-center">
            {r.toUpperCase()}
          </SelectableOption>
        ))}
      </div>
      <p className="mt-2 text-[12px] text-ink-faint">{copy.workbench.vgResolutionHint}</p>
    </fieldset>
  );
}
