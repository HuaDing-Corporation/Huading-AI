"use client";

import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { ImageUp, Loader2, Sparkles, X } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import { useRegenerateReversePrompt, useReverseFromAsset, useSaveReversePrompt, useUploadImage } from "@/lib/api/hooks";
import { useTrackedUpload } from "@/lib/api/use-tracked-upload";
import { validateImageFile } from "@/lib/api/uploads";
import { friendlyReverseError, type ReversePromptJobRead, type WorkbenchPrefill } from "@/lib/api/reverse-prompt";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { ReversePromptResultView } from "@/components/workbench/reverse-prompt-result-view";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * 提示词反推 · 图片 (REVERSE-PROMPT-UI) 工作台容器 —— 唯一 hooks 调用方。状态机：
 * 上传图片(客户端校验，走现有 /uploads/images 拿 source_asset_id) → 反推 → 结果块 + 复制/保存/重推 +
 * 「带入」6 键。FIX1：请求体仅 { source_asset_id }（BE extra="forbid"，语言/细节不进请求，输出本就给 zh+en）；
 * 成功以 result 存在为准（BE status="succeeded"），失败经 apiFetch throw 落 catch。带入落点由结果视图据
 * BE 载荷直落，经 onApplyPrefill 冒泡至 page 切模式并预填目标表单。P1 只图片，但类型/交互未写死「只图片」。
 */
export function ReversePromptForm({ onApplyPrefill }: { onApplyPrefill?: (prefill: WorkbenchPrefill) => void } = {}) {
  const uploadImg = useUploadImage();
  const source = useTrackedUpload(uploadImg.mutateAsync, (r) => r.asset_id);
  const reverse = useReverseFromAsset();
  const regen = useRegenerateReversePrompt();
  const save = useSaveReversePrompt();

  const [preview, setPreview] = useState<string | null>(null);
  const [job, setJob] = useState<ReversePromptJobRead | null>(null);
  const [saved, setSaved] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  // 预览 objectURL 生命周期：换图/卸载即回收，避免泄漏。
  const previewRef = useRef<string | null>(null);
  useEffect(() => {
    previewRef.current = preview;
  }, [preview]);
  useEffect(() => () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
  }, []);

  const onSelectFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    // 允许重选同一文件：清空 input 值。
    event.target.value = "";
    if (!file) return;
    // 客户端红线校验（按真实 MIME，不信文件名）。
    const invalid = validateImageFile(file);
    if (invalid) {
      setLocalError(invalid);
      return;
    }
    setLocalError(null);
    setJob(null);
    setSaved(false);
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    setPreview(URL.createObjectURL(file));
    void source.onUpload(file);
  };

  const clearImage = () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    setPreview(null);
    source.setValue(null);
    setJob(null);
    setSaved(false);
    setLocalError(null);
  };

  const onAnalyze = async () => {
    if (!source.value || reverse.isPending) return;
    setLocalError(null);
    setSaved(false);
    try {
      const res = await reverse.mutateAsync({ source_asset_id: source.value });
      if (!res.result) {
        setLocalError(friendlyReverseError(res.error_code));
        return;
      }
      setJob(res);
    } catch (err) {
      // 只取 ApiError.code（无稳定码→通用中文兜底）；不透传后端英文 message，绝不泄裸/英文。
      setLocalError(friendlyReverseError(err instanceof ApiError ? err.code : null));
    }
  };

  const onRegenerate = async () => {
    if (!job || regen.isPending) return;
    setLocalError(null);
    setSaved(false);
    try {
      const res = await regen.mutateAsync(job.id);
      if (!res.result) {
        setLocalError(friendlyReverseError(res.error_code));
        return;
      }
      setJob(res);
    } catch (err) {
      // 只取 ApiError.code（无稳定码→通用中文兜底）；不透传后端英文 message，绝不泄裸/英文。
      setLocalError(friendlyReverseError(err instanceof ApiError ? err.code : null));
    }
  };

  const onSave = async () => {
    if (!job || save.isPending) return;
    setLocalError(null);
    try {
      await save.mutateAsync(job.id);
      setSaved(true);
    } catch (err) {
      setLocalError(friendlyReverseError(err instanceof ApiError ? err.code : null));
    }
  };

  const uploadError = source.error;
  const analyzeDisabled = !source.value || uploadImg.isPending || reverse.isPending;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Card animateIn>
        <CardTitle>{copy.reverse.title}</CardTitle>
        <CardSubtitle className="mb-[18px] mt-1">{copy.reverse.subtitle}</CardSubtitle>

        {/* 上传区 */}
        <div className="mb-[15px]">
          <span className={labelClass}>{copy.reverse.upload}</span>
          {preview ? (
            <div className="relative overflow-hidden rounded-field border border-line-gold bg-glass-soft">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={preview} alt={copy.reverse.upload} className="max-h-56 w-full object-contain" />
              <button
                type="button"
                onClick={clearImage}
                aria-label={copy.reverse.reupload}
                className="absolute right-2 top-2 inline-flex h-8 w-8 items-center justify-center rounded-mark border border-line-gold bg-glass-fill text-ink-soft outline-none transition-colors hover:bg-white/70 focus-visible:shadow-focus-gold"
              >
                <X size={15} strokeWidth={2} />
              </button>
              {uploadImg.isPending && (
                <span className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-1.5 bg-glass-fill/85 py-1 text-[12px] text-ink-soft">
                  <Loader2 size={13} className="animate-spin" /> {copy.reverse.upload}…
                </span>
              )}
            </div>
          ) : (
            <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-field border border-dashed border-line-gold bg-glass-soft px-4 py-8 text-center outline-none transition-colors hover:bg-glass-hover focus-within:shadow-focus-gold">
              <ImageUp size={22} strokeWidth={1.6} className="text-gold-deep" />
              <span className="text-[13px] text-ink">{copy.reverse.upload}</span>
              <span className="text-[12px] text-ink-faint">{copy.reverse.uploadHint}</span>
              <input type="file" accept="image/jpeg,image/png,image/webp" className="sr-only" onChange={onSelectFile} />
            </label>
          )}
          {(localError || uploadError) && (
            <p role="alert" className="mt-2 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
              {localError ?? uploadError}
            </p>
          )}
        </div>

        {/* 目标格式（固定 seedance_2_0，只读展示；语言/细节不进请求，故无设置项） */}
        <div className="mb-[18px] flex items-center justify-between rounded-field border border-line-gold bg-glass-soft px-3 py-2">
          <span className="text-[12.5px] text-ink-soft">{copy.reverse.targetLabel}</span>
          <span className="text-[12.5px] font-medium text-ink">{copy.reverse.targetSeedance}</span>
        </div>

        <Button variant="primary" size="lg" className="w-full" onClick={() => void onAnalyze()} disabled={analyzeDisabled}>
          {reverse.isPending ? (
            <>
              <Loader2 size={18} className="animate-spin" /> {copy.reverse.analyzing}
            </>
          ) : (
            <>
              <Sparkles size={18} strokeWidth={1.8} /> {copy.reverse.analyze}
            </>
          )}
        </Button>
      </Card>

      {job?.result && (
        <ReversePromptResultView
          result={job.result}
          onApply={(prefill) => onApplyPrefill?.(prefill)}
          onRegenerate={() => void onRegenerate()}
          onSave={() => void onSave()}
          regenerating={regen.isPending}
          saving={save.isPending}
          saved={saved}
        />
      )}
    </div>
  );
}
