"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Upload } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useBgmLibrary, useUploadAudio } from "@/lib/api/hooks";
import type { VideoGenBgm } from "@/lib/api/types";
import { SelectableOption } from "@/components/ui/selectable-option";
import { WaveformPlayer } from "@/components/workbench/waveform-player";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

type BgmMode = "none" | "upload" | "library";
const MODE_OPTIONS: { id: BgmMode; label: string }[] = [
  { id: "none", label: copy.workbench.vgBgmNone },
  { id: "upload", label: copy.workbench.vgBgmUpload },
  { id: "library", label: copy.workbench.vgBgmLibrary }
];

/**
 * 视频生成 BGM 三态选择（VIDEOGEN-UI-0001，seam §2/§3）：无 / 上传(复用 /uploads/audio→asset_id) /
 * 配乐库(GET /bgm-library，原生 <audio> 试听)。经 onChange 上抛 VideoGenBgm（upload:asset_id |
 * library:track_id）或 undefined（无）。上传 object-URL 预览卸载/替换时释放防泄漏。
 */
export function BgmPicker({
  onChange,
  inputId = "vg-bgm"
}: {
  onChange: (bgm: VideoGenBgm | undefined) => void;
  inputId?: string;
}) {
  const [mode, setMode] = useState<BgmMode>("none");
  const [uploaded, setUploaded] = useState<{ asset_id: string; url: string } | null>(null);
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [playingUrl, setPlayingUrl] = useState<string | null>(null); // 同一时刻只播一首：当前活跃试听 url
  const upload = useUploadAudio();
  const library = useBgmLibrary();
  const inputRef = useRef<HTMLInputElement>(null);

  // 选择映射 → 上抛（onChange 为父级 setState，稳定）。
  useEffect(() => {
    if (mode === "upload" && uploaded) onChange({ source: "upload", asset_id: uploaded.asset_id });
    else if (mode === "library" && selectedTrackId) onChange({ source: "library", track_id: selectedTrackId });
    else onChange(undefined);
  }, [mode, uploaded, selectedTrackId, onChange]);

  // 卸载释放上传预览 URL。
  const uploadedRef = useRef(uploaded);
  useEffect(() => {
    uploadedRef.current = uploaded;
  }, [uploaded]);
  useEffect(
    () => () => {
      if (uploadedRef.current) URL.revokeObjectURL(uploadedRef.current.url);
    },
    []
  );

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    if (!file.type.startsWith("audio/")) {
      setError(copy.errors.uploadType);
      return;
    }
    const url = URL.createObjectURL(file);
    try {
      const res = await upload.mutateAsync(file);
      setUploaded((prev) => {
        if (prev) URL.revokeObjectURL(prev.url);
        return { asset_id: res.asset_id, url };
      });
    } catch (err) {
      URL.revokeObjectURL(url);
      setError(errorText(err));
    }
    if (inputRef.current) inputRef.current.value = "";
  };

  const tracks = library.data ?? [];

  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{copy.workbench.vgBgmLabel}</legend>

      <div role="group" aria-label={copy.workbench.vgBgmLabel} className="mb-2.5 grid grid-cols-3 gap-2">
        {MODE_OPTIONS.map(({ id, label }) => (
          <SelectableOption key={id} selected={mode === id} onSelect={() => setMode(id)} className="justify-center">
            {label}
          </SelectableOption>
        ))}
      </div>

      {mode === "upload" && (
        <div>
          <input
            ref={inputRef}
            id={inputId}
            type="file"
            accept="audio/*"
            className="hidden"
            onChange={(e) => void onFile(e.target.files?.[0])}
          />
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={upload.isPending}
            className="flex w-full items-center justify-center gap-2 rounded-field border border-dashed border-line-gold bg-glass-fill py-4 text-[13px] text-ink-soft transition-colors hover:bg-glass-hover disabled:pointer-events-none disabled:opacity-50"
          >
            <Upload size={16} strokeWidth={1.8} /> {upload.isPending ? copy.workbench.vgBgmUploading : copy.workbench.vgBgmUploadBtn}
          </button>
          {uploaded && (
            <div className="mt-2 flex items-center gap-2">
              <span className="flex-none text-[12px] text-ink-soft">{copy.workbench.vgBgmUploaded}</span>
              <WaveformPlayer
                url={uploaded.url}
                ariaLabel={copy.workbench.vgBgmUploadedPreview}
                isActive={playingUrl === uploaded.url}
                onPlayStart={() => setPlayingUrl(uploaded.url)}
              />
            </div>
          )}
        </div>
      )}

      {mode === "library" && (
        <div>
          {library.isLoading ? (
            <p className="text-[12.5px] text-ink-soft">{copy.workbench.vgBgmLibraryLoading}</p>
          ) : library.isError ? (
            <p role="alert" className="text-[12.5px] text-error-fg">
              {copy.workbench.vgBgmLibraryError}{" "}
              <button type="button" onClick={() => void library.refetch()} className="text-gold-deep underline">
                {copy.cover.retry}
              </button>
            </p>
          ) : tracks.length === 0 ? (
            <p className="text-[12.5px] text-ink-soft">{copy.workbench.vgBgmLibraryEmpty}</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {tracks.map((t) => {
                const sel = selectedTrackId === t.track_id;
                return (
                  <li
                    key={t.track_id}
                    className={`flex flex-wrap items-center gap-2 rounded-field border px-3 py-2 ${
                      sel ? "border-line-sel bg-chip-sel" : "border-line-gold bg-glass-fill"
                    }`}
                  >
                    <span className="text-[13px] text-ink">{t.name}</span>
                    <span className="flex-none text-[11.5px] text-ink-faint">{copy.workbench.durationSeconds(t.duration_sec)}</span>
                    <WaveformPlayer
                      url={t.preview_url}
                      ariaLabel={copy.workbench.vgBgmPreviewLabel(t.name)}
                      isActive={playingUrl === t.preview_url}
                      onPlayStart={() => setPlayingUrl(t.preview_url)}
                    />
                    <button
                      type="button"
                      onClick={() => setSelectedTrackId(t.track_id)}
                      aria-pressed={sel}
                      className={`inline-flex shrink-0 items-center gap-1 rounded-pill border px-3 py-1 text-[12px] ${
                        sel ? "border-line-sel text-gold-deep" : "border-line-gold text-ink-soft hover:bg-glass-hover"
                      }`}
                    >
                      {sel && <Check size={13} strokeWidth={2} />}
                      {sel ? copy.workbench.vgBgmSelected : copy.workbench.vgBgmSelect}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      {error && (
        <p role="alert" className="mt-2 rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
          {error}
        </p>
      )}
    </fieldset>
  );
}
