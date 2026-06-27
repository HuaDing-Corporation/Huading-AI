"use client";

import { useState } from "react";

import { EcomImageCutoutForm } from "@/components/workbench/ecom-image-cutout-form";
import { EcomImageModelForm } from "@/components/workbench/ecom-image-model-form";
import { SelectableOption } from "@/components/ui/selectable-option";
import { copy } from "@/lib/copy";

type SubTool = "cutout" | "model";

const SUBTOOLS: { id: SubTool; label: string }[] = [
  { id: "cutout", label: copy.workbench.ecomSubToolCutout },
  { id: "model", label: copy.workbench.ecomSubToolModel }
];

/**
 * 电商图 mode 容器 —— 子工具切换(白底图 / AI 模特)。segmented 用 grid-cols-2（每项半宽、窄屏
 * 不溢出，吸取 Phase1 chip 溢出教训）；切换即挂载对应工具，各自复用 EcomImageTool 外壳。
 */
export function EcomImageWorkbench() {
  const [tool, setTool] = useState<SubTool>("cutout");
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div role="group" aria-label={copy.workbench.ecomSubToolLabel} className="grid grid-cols-2 gap-2">
        {SUBTOOLS.map(({ id, label }) => (
          <SelectableOption key={id} selected={tool === id} onSelect={() => setTool(id)} className="justify-center">
            {label}
          </SelectableOption>
        ))}
      </div>
      {tool === "cutout" ? <EcomImageCutoutForm /> : <EcomImageModelForm />}
    </div>
  );
}
