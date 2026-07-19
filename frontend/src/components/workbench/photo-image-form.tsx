"use client";

import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useUploadProductImage } from "@/lib/api/hooks";
import { useGenerateConfirm } from "@/lib/api/use-generate-confirm";
import { useTrackedUpload } from "@/lib/api/use-tracked-upload";
import type { CreateVideoRequest } from "@/lib/api/types";
import { useVideoTasks } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { AiTextField } from "@/components/workbench/ai-text-field";
import { AspectRatioSelect, DEFAULT_IMAGE_ASPECT_RATIO, type ImageAspectRatio } from "@/components/workbench/aspect-ratio-select";
import { ConfirmGenerateDialog } from "@/components/workbench/confirm-generate-dialog";
import { ImagePicker } from "@/components/workbench/image-picker";
import { AiLabelToggle } from "@/components/label/ai-label-toggle";
import { useLabelTogglePreference } from "@/lib/preferences/label-toggle";
import { copy } from "@/lib/copy";

/**
 * 图片生成 / 修改 (video_mode="photo") workbench container — the third mode. Mirrors
 * the ecom form's reuse (useTrackedUpload / ConfirmGenerateDialog / errorText) but
 * the asset is an OPTIONAL reference image (upload = 换背景/修图; empty = 文生图).
 * IMAGE-ASPECT-RATIO-UI-0001：去掉「质量/尺寸」下拉，改「画面比例」（默认 1:1）；提交体带 aspect_ratio，
 * 不再带 image_quality（BE 已忽略）、image_size 可不带。结果是图片，非视频。The ONLY hooks caller here.
 */
export function PhotoImageForm({
  initialPrompt,
  onPrefillConsumed
}: { initialPrompt?: string; onPrefillConsumed?: () => void } = {}) {
  const { createAndTrack } = useVideoTasks();
  const uploadRef = useUploadProductImage();
  const refImage = useTrackedUpload(uploadRef.mutateAsync, (r) => r.image_key);

  // 提示词反推「带入 · 图片生成」注入 prompt(=fill_targets.photo.topic)；惰性消费，mount 后回调 page 清空。
  const [prompt, setPrompt] = useState(() => initialPrompt ?? "");
  // WORKBENCH-KEEPALIVE-UI-0001 · prefill 消费时机重设计（详见 new-video-form.tsx 同处注释）：面板常驻后本表单
  // 不再重挂 → 改为同步 props；消费后回调 clearPrefill → props 回落 undefined → early-return，不重复注入
  // （原 prefillConsumed ref 闩锁已删，它永不复位）。参考图 / 画面比例等其它输入切 tab 后保留。
  useEffect(() => {
    if (initialPrompt === undefined) return;
    setPrompt(initialPrompt);
    onPrefillConsumed?.();
  }, [initialPrompt, onPrefillConsumed]);
  const [aspectRatio, setAspectRatio] = useState<ImageAspectRatio>(DEFAULT_IMAGE_ASPECT_RATIO); // 默认 1:1
  const [applyLabel, setApplyLabel] = useLabelTogglePreference(); // AI 标识开关（默认关，localStorage 记忆）
  const [error, setError] = useState<string | null>(null);

  // Actual submit — runs only after the 确定生成 confirmation; owns its own errors.
  const submit = async (req: CreateVideoRequest) => {
    setError(null);
    try {
      await createAndTrack(req, req.topic ?? ""); // topic 现为可选类型（电商带货可空）；照片恒有 prompt→topic，?? "" 仅为类型收敛
    } catch (err) {
      setError(errorText(err));
    }
  };
  const confirm = useGenerateConfirm(submit);

  const onGenerate = () => {
    const trimmed = prompt.trim();
    if (!trimmed) return;
    setError(null);
    confirm.requestConfirm({
      topic: trimmed,
      video_mode: "photo",
      image_key: refImage.value ?? undefined, // optional reference image
      aspect_ratio: aspectRatio, // 画面比例（默认 1:1）；不再带 image_quality（BE 已忽略）、image_size 可不带
      apply_visible_label: applyLabel
    });
  };

  const generateDisabled = uploadRef.isPending || !prompt.trim();

  return (
    <Card animateIn>
      <CardTitle>{copy.workbench.photoTitle}</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">{copy.workbench.photoSubtitle}</CardSubtitle>

      <AiTextField
        id="photo-prompt"
        label={copy.workbench.photoPromptLabel}
        value={prompt}
        onChange={setPrompt}
        rows={4}
        placeholder={copy.workbench.photoPromptPlaceholder}
      />

      <ImagePicker
        value={refImage.value}
        onChange={refImage.setValue}
        uploading={uploadRef.isPending}
        onUpload={refImage.onUpload}
        uploadError={refImage.error}
        label={copy.workbench.photoRefLabel}
        uploadLabel={copy.workbench.photoRefUpload}
        previewAlt={copy.workbench.photoRefPreviewAlt}
        inputId="photo-ref"
      />
      <p className="mb-[15px] -mt-2 text-[12px] text-ink-faint">{copy.workbench.photoRefHint}</p>

      <AspectRatioSelect value={aspectRatio} onValueChange={setAspectRatio} />

      <AiLabelToggle checked={applyLabel} onChange={setApplyLabel} />

      {error && (
        <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      {!error && !prompt.trim() && (
        <p className="mb-3 text-[12.5px] text-ink-soft" aria-live="polite">
          {copy.workbench.photoPromptRequired}
        </p>
      )}

      <Button
        variant="primary"
        size="lg"
        className="mt-2 w-full"
        onClick={onGenerate}
        disabled={generateDisabled}
      >
        <Sparkles size={18} strokeWidth={1.8} /> {copy.workbench.generatePhoto}
      </Button>

      <ConfirmGenerateDialog
        open={confirm.open}
        request={confirm.request}
        submitting={confirm.submitting}
        onConfirm={confirm.confirm}
        onCancel={confirm.cancel}
      />
    </Card>
  );
}
