"use client";

import { type ChangeEvent, useEffect, useRef, useState } from "react";
import { Video, X } from "lucide-react";

import { ALLOWED_AVATAR_VIDEO_TYPES, validateAvatarVideo } from "@/lib/media/avatar-video";
import { copy } from "@/lib/copy";

export interface AvatarVideoPickerProps {
  value: string | null;
  onChange: (value: string | null) => void;
  uploading: boolean;
  onUpload: (file: File) => void;
  uploadError?: string | null;
  inputId?: string;
  /**
   * 可注入的预检器（默认完整 validateAvatarVideo：MIME/大小/时长/分辨率）。便于测试注入受控结果——
   * jsdom 无法解码视频、readVideoMetadata 读不到元数据，故组件测试用注入版验证「拒绝→提示不上传 / 通过→上传」的接线。
   */
  validate?: (file: File) => Promise<string | null>;
}

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * 数字人「本人出镜视频」源选择器（AVATAR-VIDEO-SOURCE-UI-0001）—— 镜像 ImagePicker：选文件→客户端预检
 * (MP4/≤10s/360p–1080p，异步读元数据)→ 通过才上传。纯 props：上传 mutation 在容器。objectURL 预览换/卸载
 * 即回收防泄漏；异步预检带竞态守卫（快速重选不被晚到结果覆盖）。
 */
export function AvatarVideoPicker({
  value,
  onChange,
  uploading,
  onUpload,
  uploadError,
  inputId = "avatar-video",
  validate = validateAvatarVideo
}: AvatarVideoPickerProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [validating, setValidating] = useState(false);
  const validateSeq = useRef(0);

  const previewRef = useRef<string | null>(null);
  useEffect(() => {
    previewRef.current = preview;
  }, [preview]);
  useEffect(
    () => () => {
      // 卸载即作废在途异步预检（如切换来源使本组件卸载）：否则晚到的 validate() 会 createObjectURL +
      // 发起已放弃的上传，泄漏 objectURL。与 clearUpload 的 seq bump 对称。
      validateSeq.current += 1;
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    },
    []
  );

  const clearUpload = () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    setPreview(null);
    setLocalError(null);
    setValidating(false);
    validateSeq.current += 1; // 作废在途预检
    if (fileInputRef.current) fileInputRef.current.value = "";
    onChange(null); // 同步清父级 asset，避免生成提交已移除的视频
  };

  const onSelectFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (fileInputRef.current) fileInputRef.current.value = ""; // 允许重选同一文件
    if (!file) return;
    setLocalError(null);
    const seq = (validateSeq.current += 1);
    setValidating(true);
    const err = await validate(file);
    if (seq !== validateSeq.current) return; // 被后续选择/清除取代
    setValidating(false);
    if (err) {
      setLocalError(err);
      return;
    }
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    setPreview(URL.createObjectURL(file));
    onUpload(file);
  };

  const error = localError ?? uploadError ?? null;

  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{copy.workbench.sourceVideo}</legend>

      <input
        ref={fileInputRef}
        id={inputId}
        type="file"
        accept={ALLOWED_AVATAR_VIDEO_TYPES.join(",")}
        className="hidden"
        onChange={onSelectFile}
      />

      {preview ? (
        <div className="flex items-center gap-3 rounded-field border border-line-gold bg-glass-fill p-2.5">
          {/* object-URL 预览本地视频 */}
          <video
            src={preview}
            aria-label={copy.workbench.videoPreviewAlt}
            controls
            muted
            playsInline
            className="h-16 w-16 flex-none rounded-mark bg-black object-cover"
          />
          <div className="min-w-0 flex-1 text-[12.5px]">
            {uploading ? (
              <span className="text-ink-soft">{copy.workbench.videoUploading}</span>
            ) : value ? (
              <span className="text-gold-deep">{copy.workbench.videoReady}</span>
            ) : (
              <span className="text-error-fg">{copy.workbench.videoUploadFailed}</span>
            )}
          </div>
          <button
            type="button"
            onClick={clearUpload}
            aria-label={copy.workbench.removeVideo}
            className="flex h-8 w-8 flex-none items-center justify-center rounded-mark text-ink-soft hover:bg-glass-hover"
          >
            <X size={16} strokeWidth={2} />
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={validating}
          className="flex w-full items-center justify-center gap-2 rounded-field border border-dashed border-line-gold bg-glass-fill py-5 text-[13px] text-ink-soft hover:bg-glass-hover disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Video size={18} strokeWidth={1.8} /> {validating ? copy.workbench.videoValidating : copy.workbench.videoUpload}
        </button>
      )}

      <p className="mt-1.5 text-[12px] text-ink-faint">{copy.workbench.videoHint}</p>

      {error && (
        <p role="alert" className="mt-2 text-[12.5px] text-error-fg">
          {error}
        </p>
      )}
    </fieldset>
  );
}
