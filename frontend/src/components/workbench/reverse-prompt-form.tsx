"use client";

import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { ImageUp, Loader2, Sparkles, Video, X } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import { errorText } from "@/lib/api/error-text";
import {
  useRegenerateReversePrompt,
  useReverseFromAsset,
  useSaveReversePrompt,
  useUploadImage,
  useUploadReverseVideo
} from "@/lib/api/hooks";
import { useTrackedUpload } from "@/lib/api/use-tracked-upload";
import { validateImageFile } from "@/lib/api/uploads";
import { validateReverseVideo } from "@/lib/media/reverse-video";
import {
  friendlyReverseError,
  getReversePromptJob,
  isReverseSettled,
  REVERSE_VIDEO_CREDITS,
  type ReversePromptJobRead,
  type WorkbenchPrefill
} from "@/lib/api/reverse-prompt";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { SelectableOption } from "@/components/ui/selectable-option";
import { ReversePromptResultView } from "@/components/workbench/reverse-prompt-result-view";
import { ReverseVideoAnalysisView } from "@/components/workbench/reverse-video-analysis";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
type SourceType = "image" | "video";

/**
 * 提示词反推 (REVERSE-PROMPT-UI / VIDEO-REVERSE-PROMPT-UI-0001) 工作台容器 —— 唯一 hooks 调用方。一个入口两模式：
 *  - 图片：完全维持现状（同步 /reverse-prompt → succeeded+result；零回归）。
 *  - 视频（一期只上传文件、无链接入口）：上传视频(/uploads/videos?purpose=reverse_prompt，客户端预检 MP4/≤200MB/1–60s)
 *    → 计费门 ConfirmDialog(100 积分/次，确认一次扣/取消不扣) → 提交(202 queued) → 轮询 GET /jobs/{id} 到终态
 *    （瞬时失败软提示、不误跳结果页）→ 结果先展示视频分析(video_analysis) 再展示 Seedance 提示词；「带入」沿用现有 fill_targets。
 * 请求体仅 { source_asset_id }（BE 据资产推 source_kind）。带入落点由结果视图据 BE 载荷直落，冒泡至 page 切模式并预填。
 */
export function ReversePromptForm({ onApplyPrefill }: { onApplyPrefill?: (prefill: WorkbenchPrefill) => void } = {}) {
  const [sourceType, setSourceType] = useState<SourceType>("image");

  // ── 图片路径（现状，零回归） ──
  const uploadImg = useUploadImage();
  const imgSource = useTrackedUpload(uploadImg.mutateAsync, (r) => r.asset_id);
  const [imgPreview, setImgPreview] = useState<string | null>(null);

  // ── 视频路径 ──
  const uploadVid = useUploadReverseVideo();
  const [videoAssetId, setVideoAssetId] = useState<string | null>(null);
  const [videoPreview, setVideoPreview] = useState<string | null>(null);
  const [videoName, setVideoName] = useState<string | null>(null);
  const [videoValidating, setVideoValidating] = useState(false);
  const [chargeOpen, setChargeOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false); // confirm→submit 在途
  const [polling, setPolling] = useState(false); // 视频 job running·轮询中
  const [pollError, setPollError] = useState(false);
  const [pollTick, setPollTick] = useState(0);
  const [videoJobId, setVideoJobId] = useState<string | null>(null);

  // ── 共享 ──
  const reverse = useReverseFromAsset();
  const regen = useRegenerateReversePrompt();
  const save = useSaveReversePrompt();
  const [job, setJob] = useState<ReversePromptJobRead | null>(null);
  const [saved, setSaved] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  // 预览 objectURL 生命周期：换/卸载即回收，避免泄漏。
  const imgPreviewRef = useRef<string | null>(null);
  const videoPreviewRef = useRef<string | null>(null);
  useEffect(() => {
    imgPreviewRef.current = imgPreview;
  }, [imgPreview]);
  useEffect(() => {
    videoPreviewRef.current = videoPreview;
  }, [videoPreview]);
  useEffect(
    () => () => {
      if (imgPreviewRef.current) URL.revokeObjectURL(imgPreviewRef.current);
      if (videoPreviewRef.current) URL.revokeObjectURL(videoPreviewRef.current);
    },
    []
  );

  // 视频异步轮询：settled 才落结果（全部完成才展示）；瞬时失败**不**误跳结果页——软提示 + 下一拍重试。
  useEffect(() => {
    if (!polling || !videoJobId) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const res = await getReversePromptJob(videoJobId);
        if (cancelled) return;
        setPollError(false);
        if (isReverseSettled(res.status)) {
          setPolling(false);
          if (res.status === "failed" || !res.result) {
            setLocalError(friendlyReverseError(res.error_code, copy.errors.reverseVideoFailed));
          } else {
            setJob(res);
          }
        } else {
          setPollTick((t) => t + 1); // 未终态 → 继续轮询
        }
      } catch {
        if (!cancelled) {
          setPollError(true);
          setPollTick((t) => t + 1); // 瞬时失败 → 软提示，下一拍重试（不放弃）
        }
      }
    }, 1500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [polling, videoJobId, pollTick]);

  const resetResult = () => {
    setJob(null);
    setSaved(false);
    setLocalError(null);
  };

  const switchSource = (next: SourceType) => {
    if (next === sourceType) return;
    setSourceType(next);
    setPolling(false); // 切来源即停任何在途轮询，避免异步落一个陈旧结果到另一模式
    setVideoJobId(null);
    resetResult();
  };

  // ── 图片：选择/清除/反推（现状） ──
  const onSelectImage = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    const invalid = validateImageFile(file);
    if (invalid) {
      setLocalError(invalid);
      return;
    }
    resetResult();
    if (imgPreviewRef.current) URL.revokeObjectURL(imgPreviewRef.current);
    setImgPreview(URL.createObjectURL(file));
    void imgSource.onUpload(file);
  };

  const clearImage = () => {
    if (imgPreviewRef.current) URL.revokeObjectURL(imgPreviewRef.current);
    setImgPreview(null);
    imgSource.setValue(null);
    resetResult();
  };

  const onAnalyzeImage = async () => {
    if (!imgSource.value || reverse.isPending) return;
    setLocalError(null);
    setSaved(false);
    try {
      const res = await reverse.mutateAsync({ source_asset_id: imgSource.value });
      if (!res.result) {
        setLocalError(friendlyReverseError(res.error_code));
        return;
      }
      setJob(res);
    } catch (err) {
      setLocalError(friendlyReverseError(err instanceof ApiError ? err.code : null));
    }
  };

  // ── 视频：选择/清除/计费门/提交 ──
  const onSelectVideo = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    resetResult();
    setVideoValidating(true);
    const invalid = await validateReverseVideo(file);
    setVideoValidating(false);
    if (invalid) {
      setLocalError(invalid);
      return;
    }
    if (videoPreviewRef.current) URL.revokeObjectURL(videoPreviewRef.current);
    setVideoPreview(URL.createObjectURL(file));
    setVideoName(file.name);
    try {
      const r = await uploadVid.mutateAsync(file);
      setVideoAssetId(r.asset_id);
    } catch (err) {
      setLocalError(errorText(err)); // 友好中文，绝不泄裸串
    }
  };

  const clearVideo = () => {
    if (videoPreviewRef.current) URL.revokeObjectURL(videoPreviewRef.current);
    setVideoPreview(null);
    setVideoName(null);
    setVideoAssetId(null);
    setVideoJobId(null);
    setPolling(false);
    resetResult();
  };

  const onConfirmVideoCharge = async () => {
    if (!videoAssetId || submitting) return;
    setSubmitting(true);
    setLocalError(null);
    setSaved(false);
    try {
      const res = await reverse.mutateAsync({ source_asset_id: videoAssetId });
      setChargeOpen(false);
      if (isReverseSettled(res.status)) {
        // 边界：后端同步就绪
        if (res.result) setJob(res);
        else setLocalError(friendlyReverseError(res.error_code, copy.errors.reverseVideoFailed));
      } else {
        setVideoJobId(res.id);
        setPollError(false);
        setPolling(true);
      }
    } catch (err) {
      setChargeOpen(false);
      setLocalError(friendlyReverseError(err instanceof ApiError ? err.code : null, copy.errors.reverseVideoFailed));
    } finally {
      setSubmitting(false);
    }
  };

  // ── 结果 保存/重推（图片） ──
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

  const isVideo = sourceType === "video";
  const analyzeImageDisabled = !imgSource.value || uploadImg.isPending || reverse.isPending;
  const videoBusy = submitting || polling;
  const analyzeVideoDisabled = !videoAssetId || uploadVid.isPending || videoValidating || videoBusy;
  const uploadError = imgSource.error;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Card animateIn>
        <CardTitle>{isVideo ? copy.reverse.videoTitle : copy.reverse.title}</CardTitle>
        <CardSubtitle className="mb-[15px] mt-1">{isVideo ? copy.reverse.videoSubtitle : copy.reverse.subtitle}</CardSubtitle>

        {/* 来源二选一（图片 / 视频） */}
        <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
          <legend className={labelClass}>{copy.reverse.sourceLabel}</legend>
          <div role="group" aria-label={copy.reverse.sourceLabel} className="grid grid-cols-2 gap-2">
            <SelectableOption selected={!isVideo} onSelect={() => switchSource("image")} className="justify-center">
              <ImageUp size={15} strokeWidth={1.8} /> {copy.reverse.sourceImage}
            </SelectableOption>
            <SelectableOption selected={isVideo} onSelect={() => switchSource("video")} className="justify-center">
              <Video size={15} strokeWidth={1.8} /> {copy.reverse.sourceVideo}
              <span className="ml-1 rounded-pill bg-chip-sel px-1.5 py-0.5 text-[10px] text-gold-deep">
                {copy.reverse.videoChargeBadge(REVERSE_VIDEO_CREDITS)}
              </span>
            </SelectableOption>
          </div>
        </fieldset>

        {/* 上传区 */}
        {isVideo ? (
          <div className="mb-[15px]">
            <span className={labelClass}>{copy.reverse.videoUpload}</span>
            {videoPreview ? (
              <div className="relative overflow-hidden rounded-field border border-line-gold bg-glass-soft">
                <video src={videoPreview} controls className="max-h-56 w-full bg-black object-contain" />
                <button
                  type="button"
                  onClick={clearVideo}
                  aria-label={copy.reverse.videoReupload}
                  className="absolute right-2 top-2 inline-flex h-8 w-8 items-center justify-center rounded-mark border border-line-gold bg-glass-fill text-ink-soft outline-none transition-colors hover:bg-white/70 focus-visible:shadow-focus-gold"
                >
                  <X size={15} strokeWidth={2} />
                </button>
                <span className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] text-ink-soft">
                  {uploadVid.isPending ? (
                    <>
                      <Loader2 size={13} className="animate-spin" /> {copy.reverse.videoUploading}
                    </>
                  ) : videoAssetId ? (
                    <>✓ {copy.reverse.videoReady}</>
                  ) : (
                    <span className="truncate">{videoName}</span>
                  )}
                </span>
              </div>
            ) : (
              <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-field border border-dashed border-line-gold bg-glass-soft px-4 py-8 text-center outline-none transition-colors hover:bg-glass-hover focus-within:shadow-focus-gold">
                {videoValidating ? <Loader2 size={22} className="animate-spin text-gold-deep" /> : <Video size={22} strokeWidth={1.6} className="text-gold-deep" />}
                <span className="text-[13px] text-ink">{videoValidating ? copy.reverse.videoValidating : copy.reverse.videoUpload}</span>
                <span className="text-[12px] text-ink-faint">{copy.reverse.videoUploadHint}</span>
                <input type="file" accept="video/mp4" className="sr-only" onChange={(e) => void onSelectVideo(e)} />
              </label>
            )}
          </div>
        ) : (
          <div className="mb-[15px]">
            <span className={labelClass}>{copy.reverse.upload}</span>
            {imgPreview ? (
              <div className="relative overflow-hidden rounded-field border border-line-gold bg-glass-soft">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={imgPreview} alt={copy.reverse.upload} className="max-h-56 w-full object-contain" />
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
                <input type="file" accept="image/jpeg,image/png,image/webp" className="sr-only" onChange={onSelectImage} />
              </label>
            )}
          </div>
        )}

        {(localError || uploadError) && (
          <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
            {localError ?? uploadError}
          </p>
        )}

        {/* 目标格式（固定 seedance_2_0，只读展示） */}
        <div className="mb-[18px] flex items-center justify-between rounded-field border border-line-gold bg-glass-soft px-3 py-2">
          <span className="text-[12.5px] text-ink-soft">{copy.reverse.targetLabel}</span>
          <span className="text-[12.5px] font-medium text-ink">{copy.reverse.targetSeedance}</span>
        </div>

        {isVideo ? (
          <Button variant="primary" size="lg" className="w-full" onClick={() => setChargeOpen(true)} disabled={analyzeVideoDisabled}>
            {videoBusy ? (
              <>
                <Loader2 size={18} className="animate-spin" /> {copy.reverse.videoAnalyzing}
              </>
            ) : (
              <>
                <Sparkles size={18} strokeWidth={1.8} /> {copy.reverse.videoAnalyze}
              </>
            )}
          </Button>
        ) : (
          <Button variant="primary" size="lg" className="w-full" onClick={() => void onAnalyzeImage()} disabled={analyzeImageDisabled}>
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
        )}

        {/* 视频轮询软提示（瞬时失败/生成中，不误跳结果页） */}
        {isVideo && polling && pollError && (
          <p className="mt-2 text-center text-[12px] text-error-fg">{copy.reverse.videoPollRetrying}</p>
        )}

        <ConfirmDialog
          open={chargeOpen}
          title={copy.reverse.videoChargeTitle}
          message={copy.reverse.videoChargeMessage(REVERSE_VIDEO_CREDITS)}
          confirmLabel={copy.reverse.videoChargeConfirm}
          submitting={submitting}
          onConfirm={() => void onConfirmVideoCharge()}
          onCancel={() => setChargeOpen(false)}
        />
      </Card>

      {/* 结果：视频分析在上，Seedance 提示词在下（FIX1：video_analysis 内嵌于 result） */}
      {job?.result?.video_analysis && <ReverseVideoAnalysisView analysis={job.result.video_analysis} />}
      {job?.result && (
        <ReversePromptResultView
          result={job.result}
          onApply={(prefill) => onApplyPrefill?.(prefill)}
          onRegenerate={() => void onRegenerate()}
          onSave={() => void onSave()}
          regenerating={regen.isPending}
          saving={save.isPending}
          saved={saved}
          hideRegenerate={job.source_kind === "video"}
        />
      )}
    </div>
  );
}
