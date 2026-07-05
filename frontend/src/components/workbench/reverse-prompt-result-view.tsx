"use client";

import { useState } from "react";
import { AlertTriangle, Copy, RefreshCw, Save, Sparkles } from "lucide-react";

import {
  fillTargetToPrefill,
  type ReversePromptFillTargetKey,
  type ReversePromptResult,
  type WorkbenchPrefill
} from "@/lib/api/reverse-prompt";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { copy } from "@/lib/copy";

const labelClass = "mb-1 block text-[12px] tracking-[.5px] text-ink-soft";

/** 单个可复制文本块（大段提示词）——标签 + 文本 + 复制按钮（各自 copied 态）。 */
function CopyableBlock({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  const doCopy = async () => {
    // 仅在 Clipboard API 存在且写入成功时才置「已复制」——避免非安全上下文(clipboard 缺失)下谎报成功态。
    if (!text || !navigator.clipboard?.writeText) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // 复制失败静默降级（不显示「已复制」）
    }
  };
  return (
    <div className="mb-3">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className={`${labelClass} mb-0`}>{label}</span>
        <Button variant="soft" size="sm" onClick={() => void doCopy()}>
          <Copy size={13} strokeWidth={2} /> {copied ? copy.reverse.copied : copy.reverse.copy}
        </Button>
      </div>
      <p className="whitespace-pre-wrap rounded-field border border-line-gold bg-glass-soft px-3 py-2 text-[13px] leading-relaxed text-ink">
        {text}
      </p>
    </div>
  );
}

/** 小字段（主体/场景/构图…）——标签 + 单行值。 */
function Field({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div className="min-w-0">
      <span className={labelClass}>{label}</span>
      <p className="truncate text-[13px] text-ink" title={value}>
        {value}
      </p>
    </div>
  );
}

function TagRow({ label, tags, note }: { label: string; tags: string[]; note?: string }) {
  if (!tags?.length) return null;
  return (
    <div className="mb-3">
      <span className={labelClass}>
        {label}
        {note ? <span className="ml-1 text-ink-faint">{note}</span> : null}
      </span>
      <div className="flex flex-wrap gap-1.5">
        {tags.map((t, i) => (
          <span
            key={`${t}-${i}`}
            className="rounded-pill border border-line-gold bg-glass-soft px-2.5 py-1 text-[12px] text-ink-soft"
          >
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}

// 6 个「带入」按键，逐一对齐 BE fill_targets 的 6 键 → 对应工作台模式。
const APPLY_BUTTONS: { key: ReversePromptFillTargetKey; label: string }[] = [
  { key: "avatar_talk", label: copy.reverse.applyAvatar },
  { key: "seedance_i2v", label: copy.reverse.applyEcomVideo },
  { key: "video_gen", label: copy.reverse.applyVideoGen },
  { key: "photo", label: copy.reverse.applyPhoto },
  { key: "ecom_model", label: copy.reverse.applyEcomModel },
  { key: "ecom_poster", label: copy.reverse.applyEcomPoster }
];

export interface ReversePromptResultViewProps {
  result: ReversePromptResult;
  onApply: (prefill: WorkbenchPrefill) => void;
  onRegenerate: () => void;
  onSave: () => void;
  regenerating?: boolean;
  saving?: boolean;
  saved?: boolean;
}

/**
 * 反推结果视图 —— 扁平结果块 + 复制/保存/重新反推 + 「带入」4 模块。核心：每个「带入」按钮据
 * fillTargetToPrefill 把 BE 载荷直落对应表单（不猜字段）；缺该模块 fill_target 即置灰。近似重建红线
 * disclaimer 必现（BE 未给则用前端兜底文案）。
 */
export function ReversePromptResultView({
  result,
  onApply,
  onRegenerate,
  onSave,
  regenerating,
  saving,
  saved
}: ReversePromptResultViewProps) {
  const confidencePct = Math.round((result.confidence ?? 0) * 100);
  const disclaimer = result.disclaimer?.trim() || copy.reverse.disclaimer;

  return (
    <Card animateIn>
      <div className="mb-2 flex items-center justify-between gap-2">
        <CardTitle>{copy.reverse.resultTitle}</CardTitle>
        <span className="shrink-0 rounded-pill border border-line-gold bg-glass-soft px-2.5 py-1 text-[12px] text-ink-soft">
          {copy.reverse.confidenceLabel} {confidencePct}%
        </span>
      </div>

      {/* 近似重建红线 —— 醒目告知不保证完全复刻原素材 */}
      <p
        role="note"
        className="mb-4 flex items-start gap-1.5 rounded-field bg-error-bg px-3 py-2 text-[12.5px] leading-relaxed text-error-fg"
      >
        <AlertTriangle size={14} strokeWidth={2} className="mt-0.5 shrink-0" />
        {disclaimer}
      </p>

      <CopyableBlock label={copy.reverse.blockPromptZh} text={result.prompt_zh} />
      <CopyableBlock label={copy.reverse.blockPromptEn} text={result.prompt_en} />
      <CopyableBlock label={copy.reverse.blockNegative} text={result.negative_prompt} />

      <div className="mb-3 grid grid-cols-2 gap-x-4 gap-y-2">
        <Field label={copy.reverse.blockSubject} value={result.subject} />
        <Field label={copy.reverse.blockScene} value={result.scene} />
        <Field label={copy.reverse.blockComposition} value={result.composition} />
        <Field label={copy.reverse.blockCamera} value={result.camera} />
        <Field label={copy.reverse.blockLighting} value={result.lighting} />
        <Field label={copy.reverse.blockMotion} value={result.motion_hint} />
      </div>

      <TagRow label={copy.reverse.blockStyle} tags={result.style_tags} />
      {result.selling_points?.length ? (
        <TagRow label={copy.reverse.blockSelling} tags={result.selling_points} />
      ) : null}
      {result.text_in_media?.length ? (
        <TagRow label={copy.reverse.blockText} tags={result.text_in_media} />
      ) : null}

      {/* 保存 / 重新反推 */}
      <div className="mb-4 mt-1 flex gap-2">
        <Button variant="soft" size="sm" onClick={onSave} disabled={saving}>
          <Save size={14} strokeWidth={2} /> {saved ? copy.reverse.saved : saving ? copy.reverse.saving : copy.reverse.save}
        </Button>
        <Button variant="soft" size="sm" onClick={onRegenerate} disabled={regenerating}>
          <RefreshCw size={14} strokeWidth={2} /> {regenerating ? copy.reverse.regenerating : copy.reverse.regenerate}
        </Button>
      </div>

      {/* 带入生成 —— 缺 fill_target 的模块置灰 */}
      <div className="rounded-card border border-line-gold bg-glass-soft p-3">
        <div className="mb-1 flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <Sparkles size={15} strokeWidth={1.8} /> {copy.reverse.applyTitle}
        </div>
        <p className="mb-2.5 text-[12px] text-ink-soft">{copy.reverse.applyHint}</p>
        <div className="grid grid-cols-2 gap-2">
          {APPLY_BUTTONS.map(({ key, label }) => {
            const prefill = fillTargetToPrefill(key, result.fill_targets);
            return (
              <Button
                key={key}
                variant="soft"
                size="sm"
                className="justify-start"
                disabled={!prefill}
                title={prefill ? undefined : copy.reverse.applyUnavailable}
                onClick={() => prefill && onApply(prefill)}
              >
                {label}
              </Button>
            );
          })}
        </div>
      </div>
    </Card>
  );
}
