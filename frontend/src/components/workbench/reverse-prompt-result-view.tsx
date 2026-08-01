"use client";

import { useState } from "react";
import { AlertTriangle, RefreshCw, Save, Sparkles } from "lucide-react";

import {
  fillTargetToPrefill,
  type ReversePromptFillTargetKey,
  type ReversePromptResult,
  type WorkbenchPrefill
} from "@/lib/api/reverse-prompt";
import { Button } from "@/components/ui/button";
import { CopyableBlock } from "@/components/ui/copyable-block";
import { Card, CardTitle } from "@/components/ui/card";
import { PrefillConfirmDialog } from "@/components/workbench/prefill-confirm-dialog";
import { copy } from "@/lib/copy";

const labelClass = "mb-1 block text-[12px] tracking-[.5px] text-ink-soft";

/** 单个可复制文本块（大段提示词）——标签 + 文本 + 复制按钮（各自 copied 态）。 */

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

// 「带入」按键，对齐 BE fill_targets → 对应工作台模式。营销海报下线（ECOM-REPLICATE-UI-0001）→ 去 ecom_poster。
const APPLY_BUTTONS: { key: ReversePromptFillTargetKey; label: string }[] = [
  { key: "avatar_talk", label: copy.reverse.applyAvatar },
  { key: "seedance_i2v", label: copy.reverse.applyEcomVideo },
  { key: "video_gen", label: copy.reverse.applyVideoGen },
  { key: "photo", label: copy.reverse.applyPhoto },
  { key: "ecom_model", label: copy.reverse.applyEcomModel }
];

export interface ReversePromptResultViewProps {
  result: ReversePromptResult;
  onApply: (prefill: WorkbenchPrefill) => void;
  onRegenerate: () => void;
  onSave: () => void;
  regenerating?: boolean;
  saving?: boolean;
  saved?: boolean;
  /** 视频反推：重新反推会二次扣费（短档 150 积分/次），一期无二次计费门 → 隐藏「重新反推」避免误扣（VIDEO-REVERSE-PROMPT-UI-0001）。 */
  hideRegenerate?: boolean;
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
  saved,
  hideRegenerate
}: ReversePromptResultViewProps) {
  const confidencePct = Math.round((result.confidence ?? 0) * 100);
  const disclaimer = result.disclaimer?.trim() || copy.reverse.disclaimer;
  // 带入前确认（D3-④）：点「带入 · X」不再直接落值，先把载荷挂到待确认态、开弹窗。
  const [pending, setPending] = useState<{ prefill: WorkbenchPrefill; label: string } | null>(null);
  // 🔴 范围4 老结构回落：BE 未给 structured_prompt（历史里大量存量结果）→ 回落既有 prompt_zh / prompt_en 展示。
  //    不许因为读了 undefined 就白屏或把 "undefined" 印到界面上（必测项）。
  //    ⚠️ 判据是**有内容**而非「键存在」：`{en:"",zh:""}` 是真值，按存在处理会渲染两个空块，还把能用的
  //    prompt_zh/prompt_en 藏起来（Code Review nit）。
  const sp = result.structured_prompt;
  const structured = sp && (sp.zh?.trim() || sp.en?.trim()) ? sp : undefined;
  const shotSummary = result.video_analysis?.shot_summary?.trim();
  // §八 M2：结构化串**不再含**分镜段（分镜走独立的 shot_section / shot_summary）→ 这里直接整串渲染。
  // 上一版为了不让同一段文字出现两次，要先 splitShotSection 把段剥掉；契约把段拆出去后，那步连同函数一起删了。
  // clamp 提示要的「原视频 N 秒」：优先 source_media（§4.1 客观事实），回落 video_analysis.duration_sec。
  const sourceDurationSec = result.source_media?.duration_sec ?? result.video_analysis?.duration_sec ?? null;

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

      {/* 主提示词（§4.3 结构化）：中文供人理解、英文供 provider 消费 → **两个复制按钮各给一份**，
          不替用户猜他要拷哪一份（拿去别的工具用的是 en，核对语义看的是 zh）。
          老结构结果无 structured_prompt → 回落既有中/英提示词块（不白屏、不 undefined）。 */}
      {structured ? (
        <>
          <CopyableBlock label={copy.reverse.blockStructuredZh} text={structured.zh} />
          <CopyableBlock label={copy.reverse.blockStructuredEn} text={structured.en} />
        </>
      ) : (
        <>
          <CopyableBlock label={copy.reverse.blockPromptZh} text={result.prompt_zh} />
          <CopyableBlock label={copy.reverse.blockPromptEn} text={result.prompt_en} />
        </>
      )}
      <CopyableBlock label={copy.reverse.blockNegative} text={result.negative_prompt} />
      {/* 视频反推分镜表（§4.1 shot_summary）：有值才渲染；无值（图片/老结构）整块不出现 */}
      {shotSummary ? <CopyableBlock label={copy.reverse.blockShotSummary} text={shotSummary} /> : null}

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
        {!hideRegenerate && (
          <Button variant="soft" size="sm" onClick={onRegenerate} disabled={regenerating}>
            <RefreshCw size={14} strokeWidth={2} /> {regenerating ? copy.reverse.regenerating : copy.reverse.regenerate}
          </Button>
        )}
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
                // D3-④：先开确认弹窗（可逐项取消/编辑），确认后才真正落值。
                onClick={() => prefill && setPending({ prefill, label })}
              >
                {label}
              </Button>
            );
          })}
        </div>
      </div>

      {/* 带入前确认 —— 本组件是**两个带入入口共用**的同一份代码（工作台反推页 + 历史详情弹窗），
          故两入口都走这同一个弹窗，行为一致是结构性的，不是靠两处各写一遍。 */}
      <PrefillConfirmDialog
        open={pending !== null}
        prefill={pending?.prefill ?? null}
        moduleLabel={pending?.label ?? ""}
        sourceDurationSec={sourceDurationSec}
        onCancel={() => setPending(null)}
        onConfirm={(next) => {
          setPending(null);
          onApply(next);
        }}
      />
    </Card>
  );
}
