"use client";

import { useState, type CSSProperties } from "react";
import { Eraser } from "lucide-react";

import { useCutoutBatch, useCutoutImage } from "@/lib/api/hooks";
import type { CutoutBackground } from "@/lib/api/types";
import { SelectableOption } from "@/components/ui/selectable-option";
import { EcomImageTool } from "@/components/workbench/ecom-image-tool";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

const BG_OPTIONS: { id: CutoutBackground; label: string }[] = [
  { id: "white", label: copy.workbench.ecomBgWhite },
  { id: "transparent", label: copy.workbench.ecomBgTransparent }
];

// 透明底棋盘格：token 化(var(--line-gold)，非硬编码 hex)体现 PNG alpha 透明。
const checkerStyle: CSSProperties = {
  backgroundImage: "repeating-conic-gradient(var(--line-gold) 0% 25%, transparent 0% 50%)",
  backgroundSize: "14px 14px"
};

/**
 * 电商图 · 白底图/抠图(Phase1) —— 复用 EcomImageTool 外壳，仅提供「白底/透明」偏好 + cutout
 * 提交 + 透明棋盘格装饰。单张 POST /ecom-images/cutout、批量 /cutout/batch 返回已创建 photo
 * task(kind=ecom_cutout)，由外壳 trackExisting 轮询（产物进 TaskList + 图片历史）。
 */
export function EcomImageCutoutForm() {
  const cutout = useCutoutImage();
  const cutoutBatch = useCutoutBatch();
  const [background, setBackground] = useState<CutoutBackground>("white");
  // 透明装饰按「提交时」的背景快照，避免提交后改选未重新生成却变了预览底纹。
  const [submittedBg, setSubmittedBg] = useState<CutoutBackground>("white");

  return (
    <EcomImageTool
      title={copy.workbench.ecomCutoutTitle}
      subtitle={copy.workbench.ecomCutoutSubtitle}
      idPrefix="ecom-cutout"
      icon={<Eraser size={18} strokeWidth={1.8} />}
      submitting={cutout.isPending || cutoutBatch.isPending}
      resultDecoration={submittedBg === "transparent" ? checkerStyle : undefined}
      onSubmitSingle={async (assetId, applyVisibleLabel) => {
        setSubmittedBg(background);
        const res = await cutout.mutateAsync({ source_asset_id: assetId, background, apply_visible_label: applyVisibleLabel });
        return [res.task_id];
      }}
      onSubmitBatch={async (assetIds, applyVisibleLabel) => {
        setSubmittedBg(background);
        const res = await cutoutBatch.mutateAsync({
          items: assetIds.map((id) => ({ source_asset_id: id, background, apply_visible_label: applyVisibleLabel }))
        });
        return res.tasks.map((t) => t.task_id);
      }}
    >
      {/* 背景：白底 / 透明 */}
      <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
        <legend className={labelClass}>{copy.workbench.ecomBgLabel}</legend>
        <div className="grid grid-cols-2 gap-2">
          {BG_OPTIONS.map(({ id, label }) => (
            <SelectableOption key={id} selected={background === id} onSelect={() => setBackground(id)} className="justify-center">
              {label}
            </SelectableOption>
          ))}
        </div>
      </fieldset>
    </EcomImageTool>
  );
}
