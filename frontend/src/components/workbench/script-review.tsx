"use client";

import { useId } from "react";

import { AiTextField } from "@/components/workbench/ai-text-field";
import { copy } from "@/lib/copy";
import { CPS, estSeconds, MAX_SCRIPT_SECONDS } from "@/lib/sse/constants";

export interface ScriptReviewProps {
  script: string;
  onChange: (script: string) => void;
  onRegenerate: () => void;
  loading: boolean;
  speed: number;
  /** Override the label — 电商带货 marks the 口播文案 as 仅配音. */
  label?: string;
  /**
   * textarea 的 DOM id（AiTextField 同时用作 name）。WORKBENCH-KEEPALIVE-UI-0001：工作台面板常驻后，口播与
   * 电商带货两份 ScriptReview 会同存于 DOM —— id 必须各自唯一，否则 `<label for>` 按 HTML 规范关联到文档中
   * **第一个**同 id 元素（即隐藏面板那份）→ 点标签无反应、且可见控件丢失 label 关联。
   * FIX1：**不传时走 useId 生成实例唯一 id**（此前默认固定 "video-script"，等于把重复的责任推给调用方——
   * 第三个无参调用照样撞，只是把爆点推到未来）。只有需要稳定 selector 的调用方（口播的 e2e 依赖
   * `#video-script`）才显式传。
   */
  id?: string;
}

/** AI 口播文案 panel — editable textarea + regenerate, with a char/duration footer.
 *  Thin wrapper over the shared AiTextField. Pure props, no fetch. */
export function ScriptReview({ script, onChange, onRegenerate, loading, speed, label, id }: ScriptReviewProps) {
  const uid = useId();
  const fieldId = id ?? uid; // 不传 → 每实例唯一，撞车不再是默认行为
  // Soft over-length hint: the raw estimate can exceed the cap (estSeconds clamps
  // to MAX_SCRIPT_SECONDS for display), so gate the warning on the unclamped value.
  const tooLong = script.length / CPS / (speed || 1) > MAX_SCRIPT_SECONDS;
  const seconds = estSeconds(script, speed);
  const footer = (
    <>
      {script.length} 字 · 约 {seconds}s
      {tooLong ? ` · ${copy.workbench.scriptTooLong(seconds)}` : ""}
    </>
  );

  return (
    <AiTextField
      id={fieldId}
      label={label ?? copy.workbench.scriptLabel}
      value={script}
      onChange={onChange}
      onAction={onRegenerate}
      actionLabel={copy.workbench.regenerate}
      actionIcon="regenerate"
      loading={loading}
      footer={footer}
    />
  );
}
