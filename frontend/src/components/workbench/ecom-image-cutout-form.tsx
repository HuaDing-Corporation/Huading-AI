"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Download, Eraser, ImagePlus, RefreshCw, X } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { friendlyImageError } from "@/lib/api/image-error";
import { useCutoutBatch, useCutoutImage, useUploadImage } from "@/lib/api/hooks";
import { useTrackedUpload } from "@/lib/api/use-tracked-upload";
import { ALLOWED_UPLOAD_TYPES, MAX_UPLOAD_BYTES } from "@/lib/api/uploads";
import type { CutoutBackground } from "@/lib/api/types";
import { useVideoTasks, type TrackedTask } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { SelectableOption } from "@/components/ui/selectable-option";
import { ImagePicker } from "@/components/workbench/image-picker";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const MAX_BATCH = 20;

type EcomMode = "single" | "batch";

const MODE_OPTIONS: { id: EcomMode; label: string }[] = [
  { id: "single", label: copy.workbench.ecomModeSingle },
  { id: "batch", label: copy.workbench.ecomModeBatch }
];
const BG_OPTIONS: { id: CutoutBackground; label: string }[] = [
  { id: "white", label: copy.workbench.ecomBgWhite },
  { id: "transparent", label: copy.workbench.ecomBgTransparent }
];

// 透明底棋盘格：token 化(var(--line-gold)，非硬编码 hex)体现 PNG alpha 透明。
const checkerStyle: CSSProperties = {
  backgroundImage: "repeating-conic-gradient(var(--line-gold) 0% 25%, transparent 0% 50%)",
  backgroundSize: "14px 14px"
};

/** 触发浏览器下载一个 URL(批量逐个触发；同 task-card 的 a download 模式)。 */
function triggerDownload(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** 单个结果瓦片(单张 + 批量网格复用)：进度 / 失败友好 / 完成(透明棋盘格预览 + 下载)。 */
function ResultTile({ task, transparent }: { task: TrackedTask; transparent: boolean }) {
  if (task.status === "failed") {
    return (
      <div className="flex flex-col items-center justify-center gap-1 rounded-field border border-line-gold bg-error-bg p-4 text-center">
        <p className="text-[12.5px] text-error-fg">{friendlyImageError(task.errorCode)}</p>
      </div>
    );
  }
  if (task.status === "done" && task.playbackUrl) {
    return (
      <div className="flex flex-col gap-2">
        <div
          className="overflow-hidden rounded-field border border-line-gold"
          style={transparent ? checkerStyle : undefined}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={task.playbackUrl} alt={copy.workbench.ecomResultsLabel} className="h-full w-full object-contain" />
        </div>
        <a
          href={task.downloadUrl ?? task.playbackUrl}
          download
          className="inline-flex w-fit items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep transition-colors hover:bg-glass-hover"
        >
          <Download size={14} strokeWidth={2} /> {copy.workbench.ecomDownload}
        </a>
      </div>
    );
  }
  // queued / running
  return (
    <div className="flex flex-col gap-1.5 rounded-field border border-line-gold bg-glass-fill p-4">
      <span className="text-[12px] text-ink-soft">{task.statusLabel}</span>
      <Progress value={task.progress} className="h-[5px]" />
    </div>
  );
}

/**
 * 电商图工作台第 5 模式 — 白底图/抠图(单张 + 批量 fan-out)。唯一 hooks 调用方。
 * 上传商品图(单/多)→ 选白底/透明 → 单/批 toggle → 生成。单张 POST /ecom-images/cutout、
 * 批量 /cutout/batch 返回已创建 photo task(kind=ecom_cutout)的 task_id,经 tasks-context
 * trackExisting 复用轮询(SSE/poll/watchdog/reconcile),产物进 TaskList + 图片历史。
 * 批量结果网格 + 批量下载(因批量计入 photo 20 上限会被裁,前端即时拿全量)。单个失败隔离。
 */
export function EcomImageCutoutForm() {
  const { tasks, trackExisting } = useVideoTasks();
  const uploadImg = useUploadImage();
  const cutout = useCutoutImage();
  const cutoutBatch = useCutoutBatch();
  const source = useTrackedUpload(uploadImg.mutateAsync, (r) => r.asset_id);

  const [mode, setMode] = useState<EcomMode>("single");
  const [background, setBackground] = useState<CutoutBackground>("white");
  const [submittedBg, setSubmittedBg] = useState<CutoutBackground>("white");
  const [batchItems, setBatchItems] = useState<{ assetId: string; preview: string }[]>([]);
  const [submittedIds, setSubmittedIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const batchInputRef = useRef<HTMLInputElement>(null);

  // 卸载时释放残留批量预览的 object URL(切走工作台模式即卸载),对齐 ImagePicker 防泄漏。
  const batchItemsRef = useRef(batchItems);
  useEffect(() => {
    batchItemsRef.current = batchItems;
  }, [batchItems]);
  useEffect(() => () => batchItemsRef.current.forEach((it) => URL.revokeObjectURL(it.preview)), []);

  const submitting = cutout.isPending || cutoutBatch.isPending;

  const onBatchFiles = async (files: FileList | null) => {
    if (!files) return;
    setError(null);
    const all = Array.from(files);
    const valid = all.filter((f) => ALLOWED_UPLOAD_TYPES.includes(f.type) && f.size <= MAX_UPLOAD_BYTES);
    // 非法文件不再静默丢弃：复用单图同款提示(对齐 ImagePicker 失败友好)。
    if (all.some((f) => !ALLOWED_UPLOAD_TYPES.includes(f.type))) setError(copy.errors.uploadType);
    else if (all.some((f) => f.size > MAX_UPLOAD_BYTES)) setError(copy.errors.uploadTooLarge);
    const room = MAX_BATCH - batchItems.length;
    if (valid.length > room) setError(copy.workbench.ecomBatchOverLimit);
    for (const file of valid.slice(0, room)) {
      const preview = URL.createObjectURL(file);
      try {
        const r = await uploadImg.mutateAsync(file);
        setBatchItems((prev) => [...prev, { assetId: r.asset_id, preview }]);
      } catch (err) {
        URL.revokeObjectURL(preview);
        setError(errorText(err));
      }
    }
    if (batchInputRef.current) batchInputRef.current.value = "";
  };

  const removeBatchItem = (index: number) => {
    setBatchItems((prev) => {
      const item = prev[index];
      if (item) URL.revokeObjectURL(item.preview);
      return prev.filter((_, i) => i !== index);
    });
  };

  const onGenerate = async () => {
    setError(null);
    setSubmittedBg(background);
    if (mode === "single") {
      if (!source.value) {
        setError(copy.workbench.ecomUploadRequired);
        return;
      }
      try {
        const res = await cutout.mutateAsync({ source_asset_id: source.value, background });
        trackExisting(res.task_id, copy.workbench.ecomCutoutTitle, "photo");
        setSubmittedIds([res.task_id]);
      } catch (err) {
        setError(errorText(err));
      }
    } else {
      if (batchItems.length === 0) {
        setError(copy.workbench.ecomUploadRequired);
        return;
      }
      try {
        const res = await cutoutBatch.mutateAsync({
          items: batchItems.map((it) => ({ source_asset_id: it.assetId, background }))
        });
        res.tasks.forEach((t) => trackExisting(t.task_id, copy.workbench.ecomCutoutTitle, "photo"));
        setSubmittedIds(res.tasks.map((t) => t.task_id));
      } catch (err) {
        setError(errorText(err));
      }
    }
  };

  const generateDisabled =
    submitting ||
    uploadImg.isPending ||
    (mode === "single" ? !source.value : batchItems.length === 0);

  // 提交的 task 实时状态(从 tasks-context)。单个失败不连累其他(各自 ResultTile)。
  const resultTasks = submittedIds
    .map((id) => tasks.find((t) => t.taskId === id))
    .filter((t): t is TrackedTask => Boolean(t));
  const transparent = submittedBg === "transparent";
  const downloadable = resultTasks.filter((t) => t.status === "done" && (t.downloadUrl ?? t.playbackUrl));

  return (
    <Card animateIn>
      <CardTitle>{copy.workbench.ecomCutoutTitle}</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">{copy.workbench.ecomCutoutSubtitle}</CardSubtitle>

      {/* 单张 / 批量 toggle */}
      <div className="mb-[15px]">
        <div className="grid grid-cols-2 gap-2">
          {MODE_OPTIONS.map(({ id, label }) => (
            <SelectableOption
              key={id}
              selected={mode === id}
              onSelect={() => {
                if (id === mode) return;
                setMode(id);
                // 切模式重置上一模式的结果/错误，避免陈旧结果与当前模式不符。
                setSubmittedIds([]);
                setError(null);
              }}
              className="justify-center"
            >
              {label}
            </SelectableOption>
          ))}
        </div>
      </div>

      {/* 上传：单张 ImagePicker / 批量多图 */}
      {mode === "single" ? (
        <ImagePicker
          value={source.value}
          onChange={source.setValue}
          uploading={uploadImg.isPending}
          onUpload={source.onUpload}
          uploadError={source.error}
          label={copy.workbench.ecomUploadLabel}
          uploadLabel={copy.workbench.ecomUploadLabel}
          previewAlt={copy.workbench.ecomUploadLabel}
          inputId="ecom-cutout-source"
        />
      ) : (
        <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
          <legend className={labelClass}>{copy.workbench.ecomUploadBatchLabel}</legend>
          <input
            ref={batchInputRef}
            id="ecom-cutout-batch"
            type="file"
            multiple
            accept={ALLOWED_UPLOAD_TYPES.join(",")}
            className="hidden"
            onChange={(e) => void onBatchFiles(e.target.files)}
          />
          {batchItems.length > 0 && (
            <div className="mb-2.5 grid grid-cols-4 gap-2 sm:grid-cols-5">
              {batchItems.map((item, i) => (
                <div key={i} className="relative overflow-hidden rounded-mark border border-line-gold">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={item.preview} alt={copy.workbench.ecomUploadLabel} className="aspect-square w-full object-cover" />
                  <button
                    type="button"
                    onClick={() => removeBatchItem(i)}
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
            onClick={() => batchInputRef.current?.click()}
            disabled={uploadImg.isPending || batchItems.length >= MAX_BATCH}
            className="flex w-full items-center justify-center gap-2 rounded-field border border-dashed border-line-gold bg-glass-fill py-5 text-[13px] text-ink-soft transition-colors hover:bg-glass-hover disabled:pointer-events-none disabled:opacity-50"
          >
            <ImagePlus size={18} strokeWidth={1.8} /> {uploadImg.isPending ? copy.workbench.ecomGenerating : copy.workbench.ecomUploadBatchLabel}
          </button>
        </fieldset>
      )}

      {/* 背景：白底 / 透明 */}
      <div className="mb-[15px]">
        <label className={labelClass}>{copy.workbench.ecomBgLabel}</label>
        <div className="grid grid-cols-2 gap-2">
          {BG_OPTIONS.map(({ id, label }) => (
            <SelectableOption key={id} selected={background === id} onSelect={() => setBackground(id)} className="justify-center">
              {label}
            </SelectableOption>
          ))}
        </div>
      </div>

      {error && (
        <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      <Button variant="primary" size="lg" className="mt-1 w-full" onClick={() => void onGenerate()} disabled={generateDisabled}>
        {submitting ? (
          <RefreshCw size={18} strokeWidth={1.8} className="animate-spin" />
        ) : (
          <Eraser size={18} strokeWidth={1.8} />
        )}
        {submitting ? copy.workbench.ecomGenerating : copy.workbench.ecomGenerate}
      </Button>

      {resultTasks.length > 0 && (
        <div className="mt-5 border-t border-line-gold pt-5">
          <div className="mb-3 flex items-center justify-between">
            <label className={labelClass + " mb-0"}>{copy.workbench.ecomResultsLabel}</label>
            {submittedIds.length > 1 && downloadable.length > 0 && (
              <Button
                variant="soft"
                size="sm"
                onClick={() => downloadable.forEach((t) => triggerDownload(t.downloadUrl ?? (t.playbackUrl as string)))}
              >
                <Download size={14} strokeWidth={2} /> {copy.workbench.ecomDownloadAll}
              </Button>
            )}
          </div>
          {submittedIds.length === 1 ? (
            <ResultTile task={resultTasks[0]} transparent={transparent} />
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {resultTasks.map((t) => (
                <ResultTile key={t.taskId} task={t} transparent={transparent} />
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
