"use client";

import { useEffect, useRef, useState } from "react";
import { ImagePlus, X } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useUploadImage } from "@/lib/api/hooks";
import { ALLOWED_UPLOAD_TYPES, MAX_UPLOAD_BYTES } from "@/lib/api/uploads";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
export const MAX_REFERENCE_IMAGES = 9;

interface RefItem {
  assetId: string;
  preview: string;
}

/**
 * 视频生成 参考图多传（VIDEOGEN-UI-0001，≤9，可增删）。复用电商图批量上传经验：每张经
 * useUploadImage(POST /uploads/images → asset_id) 取 asset_id；object-URL 预览并在卸载/移除时释放
 * 防泄漏；超 9 / 非法类型 / 过大友好提示。内部持有 {assetId,preview}，经 onChange 上抛 asset_id 列表
 * 供表单提交 reference_image_asset_ids。
 */
export function ReferenceImagesPicker({
  onChange,
  inputId = "vg-ref-images"
}: {
  onChange: (assetIds: string[]) => void;
  inputId?: string;
}) {
  const uploadImg = useUploadImage();
  const [items, setItems] = useState<RefItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // 上抛 asset_id 列表（onChange 为父级 setState，稳定引用，不触发循环）。
  useEffect(() => {
    onChange(items.map((it) => it.assetId));
  }, [items, onChange]);

  // 卸载释放残留预览 object URL（对齐 ImagePicker / EcomImageTool 防泄漏）。
  const itemsRef = useRef(items);
  useEffect(() => {
    itemsRef.current = items;
  }, [items]);
  useEffect(
    () => () => {
      itemsRef.current.forEach((it) => URL.revokeObjectURL(it.preview));
    },
    []
  );

  const onFiles = async (files: FileList | null) => {
    if (!files) return;
    setError(null);
    const all = Array.from(files);
    const valid = all.filter((f) => ALLOWED_UPLOAD_TYPES.includes(f.type) && f.size <= MAX_UPLOAD_BYTES);
    // 非法/过大不静默丢弃（对齐 ImagePicker 失败友好）。
    if (all.some((f) => !ALLOWED_UPLOAD_TYPES.includes(f.type))) setError(copy.errors.uploadType);
    else if (all.some((f) => f.size > MAX_UPLOAD_BYTES)) setError(copy.errors.uploadTooLarge);
    // ≤9 承重：room 取剩余位，超出部分不添加并提示。
    const room = MAX_REFERENCE_IMAGES - itemsRef.current.length;
    if (valid.length > room) setError(copy.workbench.vgRefOverLimit);
    for (const file of valid.slice(0, Math.max(0, room))) {
      const preview = URL.createObjectURL(file);
      try {
        const r = await uploadImg.mutateAsync(file);
        setItems((prev) => {
          if (prev.length >= MAX_REFERENCE_IMAGES) {
            URL.revokeObjectURL(preview); // 满额拒收也释放预览 URL，防泄漏
            return prev;
          }
          return [...prev, { assetId: r.asset_id, preview }];
        });
      } catch (err) {
        URL.revokeObjectURL(preview);
        setError(errorText(err));
      }
    }
    if (inputRef.current) inputRef.current.value = "";
  };

  const removeItem = (index: number) => {
    setItems((prev) => {
      const item = prev[index];
      if (item) URL.revokeObjectURL(item.preview);
      return prev.filter((_, i) => i !== index);
    });
  };

  const full = items.length >= MAX_REFERENCE_IMAGES;

  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{copy.workbench.vgRefImagesLabel}</legend>
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        multiple
        accept={ALLOWED_UPLOAD_TYPES.join(",")}
        className="hidden"
        onChange={(e) => void onFiles(e.target.files)}
      />
      {items.length > 0 && (
        <div className="mb-2.5 grid grid-cols-4 gap-2 sm:grid-cols-5">
          {items.map((item, i) => (
            <div key={item.assetId} className="relative overflow-hidden rounded-mark border border-line-gold">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={item.preview} alt={copy.workbench.vgRefImagesLabel} className="aspect-square w-full object-cover" />
              <button
                type="button"
                onClick={() => removeItem(i)}
                aria-label={copy.workbench.removeImage}
                className="absolute right-0.5 top-0.5 flex h-6 w-6 items-center justify-center rounded-mark bg-ink/50 text-white hover:bg-ink/70"
              >
                <X size={13} strokeWidth={2} />
              </button>
            </div>
          ))}
        </div>
      )}
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={uploadImg.isPending || full}
        className="flex w-full items-center justify-center gap-2 rounded-field border border-dashed border-line-gold bg-glass-fill py-5 text-[13px] text-ink-soft transition-colors hover:bg-glass-hover disabled:pointer-events-none disabled:opacity-50"
      >
        <ImagePlus size={18} strokeWidth={1.8} />{" "}
        {uploadImg.isPending ? copy.workbench.vgGenerating : `${copy.workbench.vgRefImagesUpload}（${items.length}/${MAX_REFERENCE_IMAGES}）`}
      </button>
      {error && (
        <p role="alert" className="mt-2 rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
          {error}
        </p>
      )}
    </fieldset>
  );
}
