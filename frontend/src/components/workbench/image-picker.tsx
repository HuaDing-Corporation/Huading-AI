"use client";

import { type ChangeEvent, useEffect, useRef, useState } from "react";
import { ImagePlus, X } from "lucide-react";

import { SelectableOption } from "@/components/ui/selectable-option";
import { ALLOWED_UPLOAD_TYPES, MAX_UPLOAD_BYTES } from "@/lib/api/uploads";
import type { AvatarPreset } from "@/lib/api/types";
import { copy } from "@/lib/copy";

export interface ImagePickerProps {
  value: string | null;
  onChange: (value: string | null) => void;
  uploading: boolean;
  onUpload: (file: File) => void;
  uploadError?: string | null;
  /** Preset grid (数字人口播 形象预设)；电商带货 i2v 无预设，默认空。 */
  presets?: AvatarPreset[];
  /** Field legend；默认「数字人形象」。 */
  label?: string;
  /** Upload tile CTA；默认「上传形象图」。 */
  uploadLabel?: string;
  /** Preview <img> alt；默认「形象预览」。 */
  previewAlt?: string;
  /** File input id（多表单同页保持唯一）；默认 avatar-image。 */
  inputId?: string;
}

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * Avatar source picker — preset grid (SelectableOption) + an upload tile.
 * Pure props: it validates mime/size and hands the file to `onUpload`; the
 * upload mutation lives in the container. The object-URL preview is revoked on
 * unmount and on replace so leaving with a preview doesn't leak.
 */
export function ImagePicker({
  value,
  onChange,
  uploading,
  onUpload,
  uploadError,
  presets = [],
  label = copy.workbench.avatarLabel,
  uploadLabel = copy.workbench.upload,
  previewAlt = copy.workbench.avatarPreviewAlt,
  inputId = "avatar-image"
}: ImagePickerProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  const previewRef = useRef<string | null>(null);
  useEffect(() => {
    previewRef.current = preview;
  }, [preview]);
  useEffect(
    () => () => {
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    },
    []
  );

  const clearUpload = () => {
    if (preview) URL.revokeObjectURL(preview);
    setPreview(null);
    setLocalError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
    // Also clear the parent's avatarAssetId so 生成 can't submit a removed asset
    // (two-step upload state stays consistent). (P1)
    onChange(null);
  };

  const onSelectFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setLocalError(null);

    if (!ALLOWED_UPLOAD_TYPES.includes(file.type)) {
      setLocalError(copy.errors.uploadType);
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setLocalError(copy.errors.uploadTooLarge);
      return;
    }

    if (preview) URL.revokeObjectURL(preview);
    setPreview(URL.createObjectURL(file));
    onUpload(file);
  };

  const error = localError ?? uploadError ?? null;

  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{label}</legend>

      {presets.length > 0 && (
        <div className="mb-2.5 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {presets.map((preset) => (
            <SelectableOption
              key={preset.asset_id}
              selected={value === preset.asset_id}
              onSelect={() => onChange(preset.asset_id)}
            >
              {/* preset thumbnail (remote URL) */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={preset.thumbnail_url ?? undefined}
                alt={preset.display_name}
                className="h-10 w-10 flex-none rounded-mark object-cover"
              />
              <span className="min-w-0 flex-1 truncate">{preset.display_name}</span>
            </SelectableOption>
          ))}
        </div>
      )}

      <input
        ref={fileInputRef}
        id={inputId}
        type="file"
        accept={ALLOWED_UPLOAD_TYPES.join(",")}
        className="hidden"
        onChange={onSelectFile}
      />

      {preview ? (
        <div className="flex items-center gap-3 rounded-field border border-line-gold bg-glass-fill p-2.5">
          {/* object-URL preview of the local file */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={preview}
            alt={previewAlt}
            className="h-14 w-14 flex-none rounded-mark object-cover"
          />
          <div className="min-w-0 flex-1 text-[12.5px]">
            {uploading ? (
              <span className="text-ink-soft">上传中…</span>
            ) : value ? (
              <span className="text-gold-deep">已上传，可生成</span>
            ) : (
              <span className="text-error-fg">上传失败</span>
            )}
          </div>
          <button
            type="button"
            onClick={clearUpload}
            aria-label={copy.workbench.removeImage}
            className="flex h-8 w-8 flex-none items-center justify-center rounded-mark text-ink-soft hover:bg-glass-hover"
          >
            <X size={16} strokeWidth={2} />
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="flex w-full items-center justify-center gap-2 rounded-field border border-dashed border-line-gold bg-glass-fill py-5 text-[13px] text-ink-soft hover:bg-glass-hover"
        >
          <ImagePlus size={18} strokeWidth={1.8} /> {uploadLabel}
        </button>
      )}

      {error && (
        <p role="alert" className="mt-2 text-[12.5px] text-error-fg">
          {error}
        </p>
      )}
    </fieldset>
  );
}
