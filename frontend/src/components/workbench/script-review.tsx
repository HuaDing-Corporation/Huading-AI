"use client";

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
}

/** AI 口播文案 panel — editable textarea + regenerate, with a char/duration footer.
 *  Thin wrapper over the shared AiTextField. Pure props, no fetch. */
export function ScriptReview({ script, onChange, onRegenerate, loading, speed, label }: ScriptReviewProps) {
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
      id="video-script"
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
