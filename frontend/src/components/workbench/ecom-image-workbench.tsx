"use client";

import { useEffect, useRef, useState } from "react";

import { EcomImageCutoutForm } from "@/components/workbench/ecom-image-cutout-form";
import { EcomImageModelForm } from "@/components/workbench/ecom-image-model-form";
import { EcomDetailWizard } from "@/components/workbench/ecom-detail-wizard";
import { SelectableOption } from "@/components/ui/selectable-option";
import { copy } from "@/lib/copy";

type SubTool = "cutout" | "model" | "detail";

// 营销海报下线（ECOM-REPLICATE-UI-0001）→ 换「电商详情图」子工具。仍 3 项（grid-cols-3 不变）。
const SUBTOOLS: { id: SubTool; label: string }[] = [
  { id: "cutout", label: copy.workbench.ecomSubToolCutout },
  { id: "model", label: copy.workbench.ecomSubToolModel },
  { id: "detail", label: copy.workbench.ecomSubToolDetail }
];

/**
 * 电商图 mode 容器 —— 子工具切换(白底图 / AI 模特 / 电商详情图)。segmented 用 grid-cols-3（每项等宽、
 * 窄屏不溢出）；切换即挂载对应工具。提示词反推「带入 · 电商图·AI 模特」：initialTool="model" + initialCustom
 * →AI 模特自定义补充；mount 后回调清空。（营销海报已下线，反推「带入·营销海报」按钮同步移除。）
 */
export function EcomImageWorkbench({
  initialTool,
  initialCustom,
  onPrefillConsumed
}: {
  initialTool?: "model";
  initialCustom?: string;
  onPrefillConsumed?: () => void;
} = {}) {
  const [tool, setTool] = useState<SubTool>(() => initialTool ?? "cutout");
  const prefillConsumed = useRef(false);
  useEffect(() => {
    if (!prefillConsumed.current && (initialTool !== undefined || initialCustom !== undefined)) {
      prefillConsumed.current = true;
      onPrefillConsumed?.();
    }
  }, [initialTool, initialCustom, onPrefillConsumed]);

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div role="group" aria-label={copy.workbench.ecomSubToolLabel} className="grid grid-cols-3 gap-2">
        {SUBTOOLS.map(({ id, label }) => (
          <SelectableOption key={id} selected={tool === id} onSelect={() => setTool(id)} className="justify-center">
            {label}
          </SelectableOption>
        ))}
      </div>
      {tool === "cutout" ? (
        <EcomImageCutoutForm />
      ) : tool === "model" ? (
        <EcomImageModelForm initialCustom={initialCustom} />
      ) : (
        <EcomDetailWizard />
      )}
    </div>
  );
}
