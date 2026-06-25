"use client";

import { useState } from "react";
import { Sparkles } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useUploadProductImage } from "@/lib/api/hooks";
import { useGenerateConfirm } from "@/lib/api/use-generate-confirm";
import { useTrackedUpload } from "@/lib/api/use-tracked-upload";
import type { CreateVideoRequest } from "@/lib/api/types";
import { useVideoTasks } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from "@/components/ui/select";
import { AiTextField } from "@/components/workbench/ai-text-field";
import { ConfirmGenerateDialog } from "@/components/workbench/confirm-generate-dialog";
import { ImagePicker } from "@/components/workbench/image-picker";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

const PHOTO_SIZES = ["1024x1024", "1536x1024", "1024x1536"] as const;
const PHOTO_QUALITIES = ["low", "medium", "high"] as const;

/**
 * 图片生成 / 修改 (video_mode="photo") workbench container — the third mode. Mirrors
 * the ecom form's reuse (useTrackedUpload / ConfirmGenerateDialog / errorText) but
 * the asset is an OPTIONAL reference image (upload = 换背景/修图; empty = 文生图)
 * and the submit body carries image_size + image_quality. The result is an image,
 * not a video. The ONLY hooks caller here.
 */
export function PhotoImageForm() {
  const { createAndTrack } = useVideoTasks();
  const uploadRef = useUploadProductImage();
  const refImage = useTrackedUpload(uploadRef.mutateAsync, (r) => r.image_key);

  const [prompt, setPrompt] = useState("");
  const [imageSize, setImageSize] = useState<string>(PHOTO_SIZES[0]);
  const [imageQuality, setImageQuality] = useState<string>("medium");
  const [error, setError] = useState<string | null>(null);

  // Actual submit — runs only after the 确定生成 confirmation; owns its own errors.
  const submit = async (req: CreateVideoRequest) => {
    setError(null);
    try {
      await createAndTrack(req, req.topic);
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
      image_size: imageSize,
      image_quality: imageQuality
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

      <div className="mb-[15px] grid grid-cols-2 gap-3">
        <div>
          <label className={labelClass}>{copy.workbench.photoSizeLabel}</label>
          <Select value={imageSize} onValueChange={setImageSize}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PHOTO_SIZES.map((s) => (
                <SelectItem key={s} value={s}>
                  {s.replace("x", " × ")}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <label className={labelClass}>{copy.workbench.photoQualityLabel}</label>
          <Select value={imageQuality} onValueChange={setImageQuality}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PHOTO_QUALITIES.map((q) => (
                <SelectItem key={q} value={q}>
                  {copy.workbench.photoQuality[q]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      <p className="mb-3 text-[12px] text-ink-faint">{copy.workbench.photoQualityHint}</p>

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
