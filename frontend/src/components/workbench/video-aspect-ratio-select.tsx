"use client";

import { useId } from "react";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { RatioGlyph } from "@/components/workbench/ratio-glyph";
import { copy } from "@/lib/copy";

// 视频画面比例（VIDEO-GEN-PARAMS-UI-0001 需求3）——对齐 provider apimart.py `_VALID_SIZES` 的 7 个 size：
// 6 定比 + auto（自适应），默认 auto。FIX1 真联调订正：**BE API 值是 "auto"**（VIDEO_GEN_ASPECT_RATIOS，
// schemas/videos.py:342-344），worker 在发 provider 时翻译 auto→"adaptive"（workers/video_gen.py:261）——FE 不发 adaptive。
// ⚠️ 与图片侧 AspectRatioSelect **值域不同**（图片 8 定比+auto 默认 1:1、hint 只在 auto）：视频 7 值、默认 auto、
// **hint 语义相反**（显式比例要警告裁切/重构）——故独立组件，不动图片件。auto 首位=默认且置顶。
export const VIDEO_ASPECT_RATIOS = ["auto", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"] as const;
export type VideoAspectRatio = (typeof VIDEO_ASPECT_RATIOS)[number];
export const DEFAULT_VIDEO_ASPECT_RATIO: VideoAspectRatio = "auto";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

function aspectLabel(ratio: VideoAspectRatio): string {
  return ratio === "auto" ? copy.workbench.vgAspectAdaptive : ratio;
}

/**
 * 视频画面比例选择器（复用 ui/Select，暖金 token）。每项配真比例矩形 glyph 直观表意（不靠颜色单独传达）；
 * 触发器显示当前 glyph + 值。**hint 分流**：选 auto → 自适应语义提示；选任一显式比例 → **裁切/重构警告**
 * （SPIKE 实测：同一竖图 auto(adaptive) 560×752、16:9 864×496，开头横向裁切、随后缩小加留白，不是换外框）。
 * 移动端 375 友好（下拉，非九宫格换行）；a11y 沿用站内 Select（Radix combobox，键盘/读屏可达）。
 */
export function VideoAspectRatioSelect({
  value,
  onValueChange,
  label = copy.workbench.vgAspectLabel
}: {
  value: VideoAspectRatio;
  onValueChange: (v: VideoAspectRatio) => void;
  label?: string;
}) {
  const labelId = useId();
  const triggerId = useId();
  const hintId = useId(); // Code Review a11y：hint 回连 trigger（aria-describedby），读屏聚焦即读到说明——aria-live 只播变化、不播初次/聚焦
  return (
    <div className="mb-[15px]">
      <label id={labelId} htmlFor={triggerId} className={labelClass}>
        {label}
      </label>
      <Select value={value} onValueChange={(v) => onValueChange(v as VideoAspectRatio)}>
        <SelectTrigger id={triggerId} aria-labelledby={`${labelId} ${triggerId}`} aria-describedby={hintId} className="w-full">
          <span className="flex min-w-0 items-center gap-2">
            <RatioGlyph ratio={value} sentinel="auto" />
            <SelectValue />
          </span>
        </SelectTrigger>
        <SelectContent>
          {VIDEO_ASPECT_RATIOS.map((r) => (
            <SelectItem key={r} value={r} icon={<RatioGlyph ratio={r} sentinel="auto" />}>
              {aspectLabel(r)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p id={hintId} className="mt-1.5 text-[12px] text-ink-faint" aria-live="polite">
        {value === "auto" ? copy.workbench.vgAspectAdaptiveHint : copy.workbench.vgAspectCropHint}
      </p>
    </div>
  );
}
