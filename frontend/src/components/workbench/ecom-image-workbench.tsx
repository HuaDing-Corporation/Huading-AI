"use client";

import { useEffect, useRef, useState } from "react";

import { EcomImageCutoutForm } from "@/components/workbench/ecom-image-cutout-form";
import { EcomImageModelForm } from "@/components/workbench/ecom-image-model-form";
import { EcomImagePosterForm } from "@/components/workbench/ecom-image-poster-form";
import { SelectableOption } from "@/components/ui/selectable-option";
import { copy } from "@/lib/copy";

type SubTool = "cutout" | "model" | "poster";

const SUBTOOLS: { id: SubTool; label: string }[] = [
  { id: "cutout", label: copy.workbench.ecomSubToolCutout },
  { id: "model", label: copy.workbench.ecomSubToolModel },
  { id: "poster", label: copy.workbench.ecomSubToolPoster }
];

/**
 * 电商图 mode 容器 —— 子工具切换(白底图 / AI 模特 / 营销海报)。segmented 用 grid-cols-3（每项
 * 等宽、窄屏不溢出，吸取 Phase1/2 教训）；切换即挂载对应工具，各自复用 EcomImageTool 外壳。
 * 提示词反推「带入 · 电商图」：initialTool 选定落点子工具（有海报标题→poster、否则→model），
 * initialCustom→AI 模特自定义补充、initialTitle/initialTagline→海报标题/副标；mount 后回调清空。
 */
export function EcomImageWorkbench({
  initialTool,
  initialCustom,
  initialTitle,
  initialTagline,
  onPrefillConsumed
}: {
  initialTool?: "model" | "poster";
  initialCustom?: string;
  initialTitle?: string;
  initialTagline?: string;
  onPrefillConsumed?: () => void;
} = {}) {
  const [tool, setTool] = useState<SubTool>(() => initialTool ?? "cutout");
  const prefillConsumed = useRef(false);
  useEffect(() => {
    if (
      !prefillConsumed.current &&
      (initialTool !== undefined ||
        initialCustom !== undefined ||
        initialTitle !== undefined ||
        initialTagline !== undefined)
    ) {
      prefillConsumed.current = true;
      onPrefillConsumed?.();
    }
  }, [initialTool, initialCustom, initialTitle, initialTagline, onPrefillConsumed]);

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
        <EcomImagePosterForm initialTitle={initialTitle} initialTagline={initialTagline} />
      )}
    </div>
  );
}
