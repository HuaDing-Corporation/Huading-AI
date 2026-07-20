"use client";

import type { ReactNode } from "react";
import { RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";

export interface AiTextFieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** AI 生成/重写 action — omit (with actionLabel) for a plain labeled textarea. */
  onAction?: () => void;
  actionLabel?: string;
  actionIcon?: "regenerate" | "generate";
  /** 禁用 AI action（非 loading）——如电商「AI 生成画面」缺产品图时前端友好拦（BE 会 422）。 */
  actionDisabled?: boolean;
  loading?: boolean;
  rows?: number;
  placeholder?: string;
  /** Footer line under the textarea (char count / hint). */
  footer?: ReactNode;
  /** a11y（VIDEO-GEN-PARAMS-UI-0001 Code Review）：invalid 态 + 错误说明回连（footer 内元素带 id 供引用），可选不破既有调用者。 */
  ariaInvalid?: boolean;
  ariaDescribedby?: string;
}

const labelClass = "block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * Labeled, editable textarea with an AI 生成/重写 button — shared by the 口播文案
 * (ScriptReview) and 画面提示词 fields so the "AI textarea" pattern lives in one
 * place. Pure props, no fetch; the caller owns the mutation + value.
 */
export function AiTextField({
  id,
  label,
  value,
  onChange,
  onAction,
  actionLabel,
  actionIcon = "regenerate",
  actionDisabled = false,
  loading = false,
  rows = 5,
  placeholder,
  footer,
  ariaInvalid,
  ariaDescribedby
}: AiTextFieldProps) {
  const Icon = loading || actionIcon === "regenerate" ? RefreshCw : Sparkles;
  return (
    <div className="mb-[15px]">
      <div className="mb-2 flex items-center justify-between gap-2">
        <label htmlFor={id} className={labelClass}>
          {label}
        </label>
        {onAction ? (
          <Button type="button" variant="soft" size="sm" onClick={onAction} disabled={loading || actionDisabled}>
            <Icon size={14} strokeWidth={2} className={loading ? "animate-spin" : undefined} />
            {actionLabel}
          </Button>
        ) : null}
      </div>

      <textarea
        id={id}
        name={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={rows}
        placeholder={placeholder}
        aria-invalid={ariaInvalid}
        aria-describedby={ariaDescribedby}
        className="w-full resize-y rounded-field border border-line-gold bg-glass-fill px-4 py-3 text-sm leading-relaxed text-ink outline-none transition-shadow placeholder:text-ink-faint focus:border-line-sel focus:shadow-focus-gold"
      />

      {footer ? <p className="mt-1.5 text-[12px] text-ink-faint">{footer}</p> : null}
    </div>
  );
}
