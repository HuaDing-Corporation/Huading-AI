"use client";

import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { ImageUp, Loader2, Sparkles, Video, X } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import { errorText } from "@/lib/api/error-text";
import {
  useEstimateReversePrompt,
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
  type ReversePromptEstimate,
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
 * 计费门正文（§八 M4）。三种状态一一对应，**顺序即优先级**：拿到报价 → 显示 BE 给的金额；
 * 在途 → 只说在取数；未取到/失败 → 说明为什么不能提交。
 * 🔴 后两支**一个数字都不出现** —— 不许猜一个数兜底（宁可挡住也不能报错价）。
 */
function chargeGateMessage(
  estimate: ReversePromptEstimate | undefined,
  estimating: boolean,
  kind: SourceType
): string {
  // 有报价才出数字，且**两档各说各的真话**（图片=识别画面 / 视频=分析分镜）；后两支一个数字都不出现。
  if (estimate) {
    return kind === "video"
      ? copy.reverse.videoChargeMessage(estimate.credits)
      : copy.reverse.imageChargeMessage(estimate.credits);
  }
  if (estimating) return copy.reverse.chargeEstimating;
  return copy.reverse.chargeEstimateBlocked;
}

/**
 * 提示词反推 (REVERSE-PROMPT-UI / VIDEO-REVERSE-PROMPT-UI-0001) 工作台容器 —— 唯一 hooks 调用方。一个入口两模式：
 *  - 图片：完全维持现状（同步 /reverse-prompt → succeeded+result；零回归）。
 *  - 视频（一期只上传文件、无链接入口）：上传视频(/uploads/videos?purpose=reverse_prompt，客户端预检 MP4/≤200MB/1–180s)
 *    → 计费门 ConfirmDialog(**金额由 POST /reverse-prompt/estimate 返回**，确认一次扣/取消不扣) → 提交(202 queued)
 *    → 轮询 GET /jobs/{id} 到终态（瞬时失败软提示、不误跳结果页；长视频按 segments_done/total 显示分段进度）
 *    → 结果先展示视频分析(video_analysis) 再展示 Seedance 提示词；「带入」沿用现有 fill_targets。
 *    ⚠️ 时长上限 180s 与「金额不写死」两条都是 §八 v2 修订（D2 / M4+D9）；本注释此前写的 60s 与「150 积分/次」
 *    已随契约作废——本项目栽过四连注释债（#209→#212），改数值类文案先核 BE 源码。
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
  // §八 M5 分段进度：轮询到的最近一次 segments 快照；null = 本次没有分段进度可显示（图片 / ≤60s 短视频）。
  // 🔴 仍是「如实转述」：不补齐、不推算、不按时间自增；只是把「两字段同生同灭」这条不变量收进类型里，
  //    让「有 total 没 done」这种半截状态**不可表示** —— 渲染处因此也不需要任何非空断言。
  const [segments, setSegments] = useState<{ total: number; done: number } | null>(null);
  // 计费预估（§八 M4）：金额来自 BE，前端不判档、不兜底。
  const estimate = useEstimateReversePrompt();
  /**
   * 🔴 本次可用的报价 —— 判据是「这份报价就是给**当前这条资产**的」，而不是「有 data」。
   *
   * 起因（Code Review 自审 P1）：TanStack Query v5 的 mutation **重新执行时不会清掉上一次的 `data`**
   *（pending 分支只重置 error / failureCount / isPaused）。于是「短视频报价 150 → 换成长视频 → 打开计费门」
   * 这条路径上，新报价还在途时弹窗会先显示上一条的 150，而实扣是 250 —— 正是本包存在的那类事故。
   * openChargeGate / onSelectVideo 里的 `estimate.reset()` 是生命周期侧的修法，但它依赖「每条新增路径都记得
   * reset」；此处再用**资产比对**兜一道结构性的：对不上就当没有报价（挡住提交、不显示任何金额）。
   */
  // 🔴 REVERSE-CHARGE-GATE-UI-0001 范围1：图片路也走计费门 → 比对基准改成**当前计费门针对的那条资产**
  // （视频路=videoAssetId、图片路=imgSource.value）。判据不变：报价必须是给这条资产的，否则当作没有报价
  // （挡住提交、不显示任何金额）——换素材后先显示上一条旧报价，正是 #220 抓到的那类 P1。
  const chargeAssetId = sourceType === "video" ? videoAssetId : imgSource.value;
  const quote = estimate.data && estimate.variables?.source_asset_id === chargeAssetId ? estimate.data : undefined;

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
        // §八 M5：如实记录 BE 给的分段进度。两字段必须都是数字才算数（BE 对图片/≤60s 短视频恒给 null）；
        // total>0 是防呆：真出现 0 就是 BE 出了问题，此时宁可不显示，也不印一句「第 3/0 段」。
        const { segments_total: total, segments_done: done } = res;
        setSegments(
          typeof total === "number" && typeof done === "number" && total > 0 ? { total, done } : null
        );
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
    setSegments(null); // 陈旧进度不许跨来源留在界面上
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
    estimate.reset(); // 换图 = 换素材：上一张的报价立即作废（另有 quote 的资产比对兜底，承重门2）
    if (imgPreviewRef.current) URL.revokeObjectURL(imgPreviewRef.current);
    setImgPreview(URL.createObjectURL(file));
    void imgSource.onUpload(file);
  };

  const clearImage = () => {
    if (imgPreviewRef.current) URL.revokeObjectURL(imgPreviewRef.current);
    setImgPreview(null);
    imgSource.setValue(null);
    estimate.reset(); // 清图后不留悬空报价
    resetResult();
  };

  /**
   * 图片计费门（范围1）：与视频路同一套闭环——每次打开都 reset 后重估（不缓存上一次结果），
   * 金额一律取 BE estimate 返回值（**不硬编码 30**：分档/费率是租户可覆写的 CreditRate）。
   */
  const openImageChargeGate = () => {
    if (!imgSource.value) return;
    setChargeOpen(true);
    estimate.reset(); // 先清掉上一次报价，再重新取（否则新报价回来前会先显示旧金额）
    estimate.mutate({ source_asset_id: imgSource.value });
  };

  const onAnalyzeImage = async () => {
    // 🔴 第二道闸（资金红线）：没拿到**本条资产**的报价就不许发起扣费——判据与弹窗显示同源，不会一个挡一个放。
    if (!imgSource.value || reverse.isPending || !quote) return;
    setLocalError(null);
    setSaved(false);
    try {
      const res = await reverse.mutateAsync({ source_asset_id: imgSource.value });
      setChargeOpen(false);
      if (!res.result) {
        setLocalError(friendlyReverseError(res.error_code));
        return;
      }
      setJob(res);
    } catch (err) {
      setChargeOpen(false); // 失败也关门（与视频路一致）：错误显示在表单区，不把用户困在弹窗里
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
    estimate.reset(); // 换视频 = 换档位，上一条的报价立即作废（另有 quote 的资产比对兜底，见下）
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
    setSegments(null);
    estimate.reset(); // 🔴 换视频 = 换档位：上一条视频的报价绝不许留到下一条（250 的视频显示 150 就是错价）
    resetResult();
  };

  /**
   * 打开计费门：**同时**去 BE 取本次的档位金额（§八 M4）。
   * 🔴 每次打开都重新估，不缓存上一次的结果 —— 时长变了档位就变，缓存 = 错价。
   */
  const openChargeGate = () => {
    if (!videoAssetId) return;
    setChargeOpen(true);
    estimate.reset(); // 先清掉上一次的报价，再重新取（否则新报价回来之前会先显示旧金额）
    estimate.mutate({ source_asset_id: videoAssetId });
  };

  const onConfirmVideoCharge = async () => {
    // 🔴 `!quote` 这一条是**第二道闸**：按钮已按同一判据禁用，但「没拿到（本条资产的）报价就不许发起扣费」
    //    是资金红线，不能只靠一个 disabled 属性守（承重门10）。判据与弹窗显示的完全同源，不会一个挡一个放。
    if (!videoAssetId || submitting || !quote) return;
    setSubmitting(true);
    setLocalError(null);
    setSaved(false);
    setSegments(null); // 新任务从「无进度」起步，不承接上一次的段数
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
                {copy.reverse.videoChargeBadge}
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
          <Button variant="primary" size="lg" className="w-full" onClick={openChargeGate} disabled={analyzeVideoDisabled}>
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
          <Button variant="primary" size="lg" className="w-full" onClick={openImageChargeGate} disabled={analyzeImageDisabled}>
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

        {/* 分段进度（§八 M5 + D10）—— 🔴 **两字段齐备才渲染**：图片/短视频 BE 恒给 null，此处整块消失，
            形态与改动前一致。没有任何按时间自增的假进度，进度只跟着 segments_done 走。
            🔴 FIX2 真联调修正：`segments_done` 是**已完成段数**，不是「正在分析的第几段」——BE 在进入第 N 段前
            写的是 `segments_done = N-1`（证据 backend/tests/test_reverse_prompt_pipeline.py:1860）。
            直接把它填进「正在分析第 X/Y 段」，长视频第一段期间必然显示「正在分析第 0/6 段」。
            故序号 = 已完成数 + 1，并夹在 total 内：末段跑完到整片汇总那段时间 done 已等于 total，
            此时显示「第 6/6 段」是诚实的（确实还在跑最后一步），显示「第 7/6 段」则是胡说。 */}
        {isVideo && polling && segments && (
          <div className="mt-2 text-center" aria-live="polite">
            <p className="text-[12.5px] text-ink">
              {copy.reverse.videoSegmentProgress(Math.min(segments.done + 1, segments.total), segments.total)}
            </p>
            <p className="mt-0.5 text-[12px] text-ink-faint">{copy.reverse.videoSegmentEta}</p>
          </div>
        )}
        {/* 视频轮询软提示（瞬时失败/生成中，不误跳结果页） */}
        {isVideo && polling && pollError && (
          <p className="mt-2 text-center text-[12px] text-error-fg">{copy.reverse.videoPollRetrying}</p>
        )}

        {/* 计费门（§八 M4 + REVERSE-CHARGE-GATE-UI-0001 范围1：**图片路也走这道门**）。
            金额一律取 BE estimate 的返回值（不硬编码任何档位价）；估算中 → 只说在取数、不显示任何数字；
            估算失败 → error 显示友好文案 + 禁用确认（**不猜一个数兜底**，宁可挡住也不能报错价），取消仍可用。
            两路共用同一个弹窗/同一份 estimate：标题与正文按当前档位分流（各说各的真话），其余判据完全同源。 */}
        <ConfirmDialog
          open={chargeOpen}
          title={isVideo ? copy.reverse.videoChargeTitle : copy.reverse.imageChargeTitle}
          message={chargeGateMessage(quote, estimate.isPending, sourceType)}
          confirmLabel={copy.reverse.chargeConfirm}
          submitting={submitting || (!isVideo && reverse.isPending)}
          confirmDisabled={!quote}
          error={estimate.isError ? copy.errors.reverseEstimateFailed : null}
          onConfirm={() => void (isVideo ? onConfirmVideoCharge() : onAnalyzeImage())}
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
