"use client";

import { useEffect, useState } from "react";

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
  // WORKBENCH-KEEPALIVE-UI-0001 · prefill 消费时机重设计：工作台面板改为「挂载后常驻」→ 本容器不再随切 mode 重挂，
  // mount-时惰性初始化承接不了后到的 prefill。改为同步 props：initialTool 切到对应子工具。
  // ⚠️ 时序：custom 由子组件 EcomImageModelForm 消费并上报 —— React effect 是「子先父后」，若这里抢先回调
  // clearPrefill，initialCustom 会在子组件挂载前就回落 undefined → 值丢失。故只有本次 prefill **不带 custom** 时，
  // 才由这里直接结清缓冲。（原 prefillConsumed ref 闩锁已删：与实例同寿，常驻后永不复位。）
  useEffect(() => {
    if (initialTool === undefined) return;
    setTool(initialTool);
    if (initialCustom === undefined) onPrefillConsumed?.();
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
        <EcomImageModelForm initialCustom={initialCustom} onPrefillConsumed={onPrefillConsumed} />
      ) : (
        <EcomDetailWizard />
      )}
    </div>
  );
}
