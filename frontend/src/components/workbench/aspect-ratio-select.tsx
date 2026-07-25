"use client";

import { useId } from "react";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { RatioGlyph } from "@/components/workbench/ratio-glyph";
import { copy } from "@/lib/copy";

// 画面比例（IMAGE-ASPECT-RATIO-UI-0001）——对齐 BE RequestedImageAspectRatio：8 定比 + auto(自适应)，默认 1:1。
export const IMAGE_ASPECT_RATIOS = ["1:1", "4:3", "3:2", "16:9", "21:9", "3:4", "2:3", "9:16", "auto"] as const;
export type ImageAspectRatio = (typeof IMAGE_ASPECT_RATIOS)[number];
export const DEFAULT_IMAGE_ASPECT_RATIO: ImageAspectRatio = "1:1";

/**
 * 是否为合法**图片**画面比例（REVERSE-DEEP-UI-0001）——外部来的值（如反推带入的 aspect_ratio）进控件前兜一道。
 * ⚠️ 图片这套（8 档 + auto）与视频那套（7 档 + auto）**不是同一组**，别互相套用。
 * 判据与枚举常量同处 → 不会漂移；用法对齐同仓 `isValidDuration`（与其档位常量一起导出）。
 */
export function isImageAspectRatio(v: string): v is ImageAspectRatio {
  return (IMAGE_ASPECT_RATIOS as readonly string[]).includes(v);
}

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

function aspectLabel(ratio: ImageAspectRatio): string {
  return ratio === "auto" ? copy.workbench.aspectAuto : ratio;
}

/**
 * 画面比例选择器（复用 ui/Select，暖金 token）——图片生成/电商白底图/电商模特图 三处复用。每项配真比例矩形
 * glyph 直观表意（不靠颜色单独传达），触发器显示当前比例 glyph + 值；选「自适应」时展开简短语义 hint。
 * 移动端 375 友好（下拉，非九宫格换行）；a11y 沿用站内 Select（Radix combobox，键盘/读屏可达）。
 */
export function AspectRatioSelect({
  value,
  onValueChange,
  label = copy.workbench.aspectLabel
}: {
  value: ImageAspectRatio;
  onValueChange: (v: ImageAspectRatio) => void;
  label?: string;
}) {
  // 程序化关联：label→trigger（点击聚焦）+ combobox 可及名 = 「画面比例 + 当前值」（自引用 triggerId 保留值）。
  const labelId = useId();
  const triggerId = useId();
  return (
    <div className="mb-[15px]">
      <label id={labelId} htmlFor={triggerId} className={labelClass}>
        {label}
      </label>
      <Select value={value} onValueChange={(v) => onValueChange(v as ImageAspectRatio)}>
        <SelectTrigger id={triggerId} aria-labelledby={`${labelId} ${triggerId}`} className="w-full">
          <span className="flex min-w-0 items-center gap-2">
            <RatioGlyph ratio={value} sentinel="auto" />
            <SelectValue />
          </span>
        </SelectTrigger>
        <SelectContent>
          {IMAGE_ASPECT_RATIOS.map((r) => (
            <SelectItem key={r} value={r} icon={<RatioGlyph ratio={r} sentinel="auto" />}>
              {aspectLabel(r)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {value === "auto" && <p className="mt-1.5 text-[12px] text-ink-faint">{copy.workbench.aspectAutoHint}</p>}
    </div>
  );
}
