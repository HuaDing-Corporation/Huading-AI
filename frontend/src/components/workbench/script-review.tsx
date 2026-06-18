"use client";

import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { copy } from "@/lib/copy";
import { CPS, estSeconds, MAX_SCRIPT_SECONDS } from "@/lib/sse/constants";

export interface ScriptReviewProps {
  script: string;
  onChange: (script: string) => void;
  onRegenerate: () => void;
  loading: boolean;
  speed: number;
}

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/** AI script panel — editable textarea + regenerate. Pure props, no fetch. */
export function ScriptReview({ script, onChange, onRegenerate, loading, speed }: ScriptReviewProps) {
  // Soft over-length hint: the raw estimate can exceed the cap (estSeconds clamps
  // to MAX_SCRIPT_SECONDS for display), so gate the warning on the unclamped value.
  const tooLong = script.length / CPS / (speed || 1) > MAX_SCRIPT_SECONDS;

  return (
    <div className="mb-[15px]">
      <div className="mb-2 flex items-center justify-between gap-2">
        <label htmlFor="video-script" className={`${labelClass} mb-0`}>
          {copy.workbench.scriptLabel}
        </label>
        <Button
          type="button"
          variant="soft"
          size="sm"
          onClick={onRegenerate}
          disabled={loading}
        >
          <RefreshCw size={14} strokeWidth={2} className={loading ? "animate-spin" : undefined} />
          {copy.workbench.regenerate}
        </Button>
      </div>

      <textarea
        id="video-script"
        name="video-script"
        value={script}
        onChange={(e) => onChange(e.target.value)}
        rows={5}
        className="w-full resize-y rounded-field border border-line-gold bg-glass-fill px-4 py-3 text-sm leading-relaxed text-ink outline-none transition-shadow placeholder:text-ink-faint focus:border-line-sel focus:shadow-focus-gold"
      />

      <p className="mt-1.5 text-[12px] text-ink-faint">
        {script.length} 字 · 约 {estSeconds(script, speed)}s
        {tooLong ? ` · ${copy.workbench.scriptTooLong(estSeconds(script, speed))}` : ""}
      </p>
    </div>
  );
}
