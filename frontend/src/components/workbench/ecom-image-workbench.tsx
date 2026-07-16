"use client";

import { useCallback, useEffect, useState } from "react";

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
const DEFAULT_SUBTOOL: SubTool = "cutout";

/**
 * 电商图 mode 容器 —— 子工具切换(白底图 / AI 模特 / 电商详情图)。segmented 用 grid-cols-3（每项等宽、
 * 窄屏不溢出）。提示词反推「带入 · 电商图·AI 模特」：initialTool="model" + initialCustom →AI 模特自定义补充。
 * （营销海报已下线，反推「带入·营销海报」按钮同步移除。）
 *
 * ECOM-SUBTOOL-KEEPALIVE-UI-0001 · 惰性挂载 + 挂载后常驻（与顶层 mode 同一套模式，见 (app)/page.tsx）：
 * 旧的条件渲染切子工具即卸载 = React state 全销毁 —— 白底图/AI 模特丢掉已传的图与参数；详情图向导更狠，
 * 连**已产出/已扣费的整单（job：plan.outputs + total_credits）**一起丢，用户付了钱的东西凭空消失。
 * 改为：某子工具首次被访问才入列挂载（不把三个子工具的 mount 请求一次性打出），此后仅隐藏、不卸载 → 输入保活。
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
  const [tool, setTool] = useState<SubTool>(() => initialTool ?? DEFAULT_SUBTOOL);
  // 惰性挂载：首屏只挂当前子工具 —— 全子树唯一的 mount 请求是 AI 模特的 GET /ecom-images/model-styles
  // （ecom-image-model-form.tsx 的 useModelStyles，enabled=!!session），不能让它随白底图一起打出去。
  // 初值必须与 tool 同源：若因 prefill 直接落在 model，它得在同一 commit 内挂载才能接住 initialCustom。
  const [mounted, setMounted] = useState<SubTool[]>(() => [initialTool ?? DEFAULT_SUBTOOL]);
  const activate = useCallback((next: SubTool) => {
    setMounted((prev) => (prev.includes(next) ? prev : [...prev, next]));
    setTool(next);
  }, []);

  // WORKBENCH-KEEPALIVE-UI-0001 · prefill 消费时机重设计：工作台面板改为「挂载后常驻」→ 本容器不再随切 mode 重挂，
  // mount-时惰性初始化承接不了后到的 prefill。改为同步 props：initialTool 切到对应子工具。
  // ⚠️ 时序：custom 由子组件 EcomImageModelForm 消费并上报 —— React effect 是「子先父后」，若这里抢先回调
  // clearPrefill，initialCustom 会在子组件挂载前就回落 undefined → 值丢失。故只有本次 prefill **不带 custom** 时，
  // 才由这里直接结清缓冲。（原 prefillConsumed ref 闩锁已删：与实例同寿，常驻后永不复位。）
  // 🔴 ECOM-SUBTOOL-KEEPALIVE：这里必须走 activate 而非裸 setTool —— 子工具改按 mounted 列表渲染后，「只切 tool
  // 不入列」= 目标子工具永不挂载 → 永不消费 initialCustom、永不上报 → clearPrefill 永不调用 → 父级缓冲永久滞留
  // （本 effect 的 deps 此后不再变化，无法自愈），且面板会空白（无任何子工具命中）。
  useEffect(() => {
    if (initialTool === undefined) return;
    activate(initialTool);
    if (initialCustom === undefined) onPrefillConsumed?.();
  }, [initialTool, initialCustom, onPrefillConsumed, activate]);

  const renderSubTool = (m: SubTool) => {
    switch (m) {
      case "cutout":
        return <EcomImageCutoutForm />;
      case "model":
        return <EcomImageModelForm initialCustom={initialCustom} onPrefillConsumed={onPrefillConsumed} />;
      case "detail":
        return <EcomDetailWizard />;
    }
  };

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div role="group" aria-label={copy.workbench.ecomSubToolLabel} className="grid grid-cols-3 gap-2">
        {SUBTOOLS.map(({ id, label }) => (
          <SelectableOption key={id} selected={tool === id} onSelect={() => activate(id)} className="justify-center">
            {label}
          </SelectableOption>
        ))}
      </div>
      {/* 常驻子工具面板（与顶层 mode 逐字同一套写法）：隐藏一律用 HTML `hidden` **属性**（UA 样式
          [hidden]{display:none}）而非 Tailwind hidden class —— 只有 display:none 能同时做到「从可及性树移除 +
          Tab 键跳过 + 不占位」；隐藏态**不挂任何设 display 的 class**，否则 CSS 会盖掉 [hidden] 的 UA 样式。
          激活态用 display:contents 让 wrapper 透明 → 子工具本体仍是外层 flex 的直接子项（gap-3 照旧作用在它身上），
          布局与改造前逐像素一致。key 恒为子工具 id，不可掺变量（一变即重挂 = state 全丢，正是本包要消灭的）。 */}
      {mounted.map((m) => {
        const active = m === tool;
        return (
          <div key={m} data-testid={`subtool-${m}`} hidden={!active} className={active ? "contents" : undefined}>
            {renderSubTool(m)}
          </div>
        );
      })}
    </div>
  );
}
