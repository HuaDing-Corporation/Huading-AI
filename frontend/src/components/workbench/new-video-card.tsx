"use client";

import { type ChangeEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { Check, ImagePlus, Sparkles, X } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import {
  ALLOWED_UPLOAD_TYPES,
  MAX_UPLOAD_BYTES,
  uploadImage
} from "@/lib/api/uploads";
import type { CreateVideoRequest, VideoMode } from "@/lib/api/types";
import { useVideoTasks } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { templateOptions, voiceSizeOptions } from "@/lib/mock";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

const VIDEO_MODES: { value: VideoMode; short: string; title: string; seedance: boolean }[] = [
  { value: "static_template", short: "静态", title: "静态模板（HTML 帧）", seedance: false },
  { value: "seedance_t2v", short: "文生", title: "Seedance 文生视频", seedance: true },
  { value: "seedance_i2v", short: "图生", title: "Seedance 图生视频（需商品图）", seedance: true }
];

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className="mb-[15px]">
      <label htmlFor={htmlFor} className={labelClass}>
        {label}
      </label>
      {children}
    </div>
  );
}

function FieldGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{label}</legend>
      {children}
    </fieldset>
  );
}

export function NewVideoCard() {
  const { createAndTrack } = useVideoTasks();

  const [topic, setTopic] = useState("秋冬新款羊绒大衣 · 卖点种草");
  const [videoMode, setVideoMode] = useState<VideoMode>("static_template");
  // Template/voice chips remain UI affordances (not yet wired to engine params).
  const [template, setTemplate] = useState(templateOptions[0]);
  const [options, setOptions] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // i2v product image
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [imageKey, setImageKey] = useState<string | null>(null);
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // B0: revoke the object URL on unmount so leaving with a preview doesn't leak.
  // clearImage/replace already revoke during normal use; revoke is a no-op on an
  // already-revoked URL, so this is safe.
  const previewUrlRef = useRef<string | null>(null);
  useEffect(() => {
    previewUrlRef.current = imagePreview;
  }, [imagePreview]);
  useEffect(
    () => () => {
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    },
    []
  );

  const isI2v = videoMode === "seedance_i2v";

  const toggleOption = (value: string) =>
    setOptions((prev) =>
      prev.includes(value) ? prev.filter((item) => item !== value) : [...prev, value]
    );

  const clearImage = () => {
    if (imagePreview) URL.revokeObjectURL(imagePreview);
    setImageKey(null);
    setImagePreview(null);
    setUploadError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const onSelectFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploadError(null);

    if (!ALLOWED_UPLOAD_TYPES.includes(file.type)) {
      setUploadError("仅支持 JPG / PNG / WebP 图片。");
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setUploadError("图片过大，请控制在 10MB 以内。");
      return;
    }

    setUploading(true);
    if (imagePreview) URL.revokeObjectURL(imagePreview);
    setImagePreview(URL.createObjectURL(file));
    try {
      const result = await uploadImage(file);
      setImageKey(result.key);
    } catch (err) {
      setImageKey(null);
      setUploadError(err instanceof ApiError ? err.message : "上传失败，请重试。");
    } finally {
      setUploading(false);
    }
  };

  const onGenerate = async () => {
    const trimmed = topic.trim();
    if (!trimmed || submitting) return;
    setError(null);
    setSubmitting(true);

    let request: CreateVideoRequest;
    if (videoMode === "static_template") {
      request = { topic: trimmed, video_mode: "static_template", pipeline: "standard", n_scenes: 3 };
    } else if (videoMode === "seedance_t2v") {
      request = { topic: trimmed, video_mode: "seedance_t2v", n_scenes: 2 };
    } else {
      request = { topic: trimmed, video_mode: "seedance_i2v", image_key: imageKey, n_scenes: 2 };
    }

    try {
      await createAndTrack(request, trimmed);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "提交失败，请重试。");
    } finally {
      setSubmitting(false);
    }
  };

  const generateDisabled = submitting || !topic.trim() || (isI2v && (!imageKey || uploading));

  return (
    <Card animateIn>
      <CardTitle>新建视频</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">支持主题生成 / 商品驱动 / 自定义脚本</CardSubtitle>

      <Field label="视频主题" htmlFor="video-topic">
        <Input
          id="video-topic"
          name="video-topic"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="输入视频主题，如：秋冬新款羊绒大衣 · 卖点种草"
        />
      </Field>

      <FieldGroup label="生成模式">
        <div className="flex gap-2">
          {VIDEO_MODES.map((m) => {
            const active = videoMode === m.value;
            return (
              <button
                key={m.value}
                type="button"
                title={m.title}
                aria-pressed={active}
                onClick={() => setVideoMode(m.value)}
                className={cn(
                  "flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-chip border px-2 py-2.5 text-[13px] outline-none transition-colors focus-visible:shadow-focus-gold",
                  active
                    ? "border-line-sel bg-chip-sel font-medium text-gold-deep"
                    : "border-line-gold bg-glass-fill text-ink-soft hover:bg-glass-hover"
                )}
              >
                {m.seedance && (
                  <span className="rounded-[6px] bg-grad-gold px-1 py-px text-[9px] font-semibold leading-none text-ink">
                    SD
                  </span>
                )}
                {m.short}
                {active && <Check size={14} strokeWidth={2.2} />}
              </button>
            );
          })}
        </div>
      </FieldGroup>

      {isI2v && (
        <Field label="商品图（图生视频输入）" htmlFor="product-image">
          <input
            ref={fileInputRef}
            id="product-image"
            type="file"
            accept={ALLOWED_UPLOAD_TYPES.join(",")}
            className="hidden"
            onChange={onSelectFile}
          />
          {imagePreview ? (
            <div className="flex items-center gap-3 rounded-field border border-line-gold bg-glass-fill p-2.5">
              {/* object-URL preview of the local file */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={imagePreview}
                alt="商品图预览"
                className="h-14 w-14 flex-none rounded-mark object-cover"
              />
              <div className="min-w-0 flex-1 text-[12.5px]">
                {uploading ? (
                  <span className="text-ink-soft">上传中…</span>
                ) : imageKey ? (
                  <span className="text-gold-deep">已上传，可生成</span>
                ) : (
                  <span className="text-error-fg">上传失败</span>
                )}
              </div>
              <button
                type="button"
                onClick={clearImage}
                aria-label="移除图片"
                className="flex h-8 w-8 flex-none items-center justify-center rounded-mark text-ink-soft hover:bg-glass-hover"
              >
                <X size={16} strokeWidth={2} />
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className={cn(
                "flex w-full items-center justify-center gap-2 rounded-field border border-dashed",
                "border-line-gold bg-glass-fill py-5 text-[13px] text-ink-soft hover:bg-glass-hover"
              )}
            >
              <ImagePlus size={18} strokeWidth={1.8} /> 上传商品图（JPG / PNG / WebP，≤10MB）
            </button>
          )}
          {uploadError && (
            <p role="alert" className="mt-2 text-[12.5px] text-error-fg">
              {uploadError}
            </p>
          )}
        </Field>
      )}

      {videoMode === "static_template" && (
        <FieldGroup label="视觉模板">
          <div className="flex flex-wrap gap-2">
            {templateOptions.map((option) => (
              <Chip key={option} selected={template === option} onClick={() => setTemplate(option)}>
                {option}
                {template === option && <Check size={16} strokeWidth={2} />}
              </Chip>
            ))}
          </div>
        </FieldGroup>
      )}

      <FieldGroup label="语音 / 尺寸">
        <div className="flex flex-wrap gap-2">
          {voiceSizeOptions.map((option) => (
            <Chip key={option} selected={options.includes(option)} onClick={() => toggleOption(option)}>
              {option}
              {options.includes(option) && <Check size={16} strokeWidth={2} />}
            </Chip>
          ))}
        </div>
      </FieldGroup>

      {error && (
        <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      <Button
        variant="primary"
        size="lg"
        className="mt-2 w-full"
        onClick={onGenerate}
        disabled={generateDisabled}
      >
        <Sparkles size={18} strokeWidth={1.8} /> {submitting ? "提交中…" : "生成视频"}
      </Button>
    </Card>
  );
}
