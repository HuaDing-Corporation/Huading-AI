"use client";

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Download, ImagePlus, RefreshCw, X } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { friendlyImageError } from "@/lib/api/image-error";
import { useUploadImage } from "@/lib/api/hooks";
import { useTrackedUpload } from "@/lib/api/use-tracked-upload";
import { ALLOWED_UPLOAD_TYPES, MAX_UPLOAD_BYTES } from "@/lib/api/uploads";
import { useVideoTasks, type TrackedTask } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { SelectableOption } from "@/components/ui/selectable-option";
import { ImagePicker } from "@/components/workbench/image-picker";
import { AiLabelToggle } from "@/components/label/ai-label-toggle";
import { useLabelTogglePreference } from "@/lib/preferences/label-toggle";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const MAX_BATCH = 20;

type EcomMode = "single" | "batch";

const MODE_OPTIONS: { id: EcomMode; label: string }[] = [
  { id: "single", label: copy.workbench.ecomModeSingle },
  { id: "batch", label: copy.workbench.ecomModeBatch }
];

/** 触发浏览器下载一个 URL(批量逐个触发；同 task-card 的 a download 模式)。 */
export function triggerDownload(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/**
 * 单个结果瓦片(单张 + 批量网格复用)：进度 / 失败友好 / 完成(可选装饰预览 + 下载)。
 * 导出供 AI 模特表单复用（ECOM-MODEL-OPTIMIZE-UI-0001：AI 模特改为多图单任务、不再走本外壳的单/批上传，
 * 但结果瓦片机制照旧复用而非重造——纯导出、零行为改动，白底图不受影响）。
 */
export function ResultTile({ task, decoration }: { task: TrackedTask; decoration?: CSSProperties }) {
  if (task.status === "failed") {
    return (
      <div role="alert" className="flex flex-col items-center justify-center gap-1 rounded-field border border-line-gold bg-error-bg p-4 text-center">
        <p className="text-[12.5px] text-error-fg">{friendlyImageError(task.errorCode)}</p>
      </div>
    );
  }
  if (task.status === "done" && task.playbackUrl) {
    return (
      <div className="flex flex-col gap-2">
        <div className="overflow-hidden rounded-field border border-line-gold" style={decoration}>
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
  // cancelled：终态中性瓦片（不显进度条，避免像卡住的进行中；ECOM-HISTORY-CANCELLED-FIX-0001 一致性补全）。
  if (task.status === "cancelled") {
    return (
      <div className="flex items-center justify-center rounded-field border border-line-gold bg-glass-fill p-4 text-center">
        <span className="text-[12.5px] text-ink-faint">{task.statusLabel}</span>
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

export interface EcomImageToolProps {
  title: string;
  subtitle: string;
  /** 工具专属偏好控件（背景 / 性别+风格+补充），渲染在上传与生成之间。 */
  children?: ReactNode;
  /** 合规提示（AI 模特有，白底图无）。 */
  complianceHint?: string;
  /** id 前缀，保证多表单同页 input id 唯一（"ecom-cutout" / "ecom-model"）。 */
  idPrefix: string;
  /** 生成按钮非 loading 时的图标。 */
  icon: ReactNode;
  /** 工具提交进行中（其 mutation isPending）。 */
  submitting: boolean;
  /** 工具侧附加校验门（如风格必填）；缺省 true。 */
  extraValid?: boolean;
  /** 单张提交：工具调用自身 mutation，返回已创建 task_id 列表。applyVisibleLabel = AI 标识开关值。 */
  onSubmitSingle: (assetId: string, applyVisibleLabel: boolean) => Promise<string[]>;
  /** 批量提交：返回已创建 task_id 列表。applyVisibleLabel 贯穿每项。 */
  onSubmitBatch: (assetIds: string[], applyVisibleLabel: boolean) => Promise<string[]>;
  /** 完成结果瓦片可选装饰（白底图透明→棋盘格）。 */
  resultDecoration?: CSSProperties;
}

/**
 * 电商图工具共享外壳（Phase1 白底图 + Phase2 AI 模特复用）—— 单/批 toggle、单图 ImagePicker /
 * 批量多图上传、生成(防连点)、结果(单/网格)、批量下载(ref 锁防连点)、单失败隔离。唯一上传/轮询
 * 调用方：useUploadImage + tasks-context.trackExisting（产物进 TaskList + 图片历史）。工具差异
 * (偏好控件 children / 提交闭包 / 装饰 / 合规) 经 props 注入；新增电商图工具勿重复造此机制。
 */
export function EcomImageTool({
  title,
  subtitle,
  children,
  complianceHint,
  idPrefix,
  icon,
  submitting,
  extraValid = true,
  onSubmitSingle,
  onSubmitBatch,
  resultDecoration
}: EcomImageToolProps) {
  const { tasks, trackExisting } = useVideoTasks();
  const uploadImg = useUploadImage();
  const source = useTrackedUpload(uploadImg.mutateAsync, (r) => r.asset_id);
  const [applyLabel, setApplyLabel] = useLabelTogglePreference(); // AI 标识开关（默认关，localStorage 记忆）

  const [mode, setMode] = useState<EcomMode>("single");
  const [batchItems, setBatchItems] = useState<{ assetId: string; preview: string }[]>([]);
  const [submittedIds, setSubmittedIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [downloadingAll, setDownloadingAll] = useState(false);
  const batchInputRef = useRef<HTMLInputElement>(null);
  // 同步锁：fireEvent / 快速连点会绕过 disabled，靠 ref 保证一次只触发一批下载（防连点）。
  const downloadingAllRef = useRef(false);
  const downloadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 卸载时释放残留批量预览的 object URL，对齐 ImagePicker 防泄漏；并清掉批量下载复位定时器，避免卸载后 setState。
  // ECOM-SUBTOOL-KEEPALIVE-UI-0001：原注释写的「切走工具/模式即卸载」前提**已不成立** —— 子工具与顶层 mode
  // 都改为「挂载后常驻」，本 cleanup 只在离开工作台（整页卸载）时才跑。这正是本包想要的：切走子工具再回来，
  // 批量预览必须还在。代价是 object URL 活到离开页面为止；用户主动移除/换图时仍会照常 revoke，无泄漏放大。
  const batchItemsRef = useRef(batchItems);
  useEffect(() => {
    batchItemsRef.current = batchItems;
  }, [batchItems]);
  useEffect(
    () => () => {
      batchItemsRef.current.forEach((it) => URL.revokeObjectURL(it.preview));
      if (downloadTimerRef.current) clearTimeout(downloadTimerRef.current);
    },
    []
  );

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
    const assetIds = mode === "single" ? (source.value ? [source.value] : []) : batchItems.map((it) => it.assetId);
    if (assetIds.length === 0) {
      setError(copy.workbench.ecomUploadRequired);
      return;
    }
    // 附加门（如风格必填）复核：正常 UI 已 disabled，这里防 fireEvent/快速绕过 disabled
    // 提交不完整请求（对齐 new-video-form/ecom-video-form 在 onGenerate 顶部复核完整门）。
    if (!extraValid) return;
    try {
      const ids = mode === "single" ? await onSubmitSingle(assetIds[0], applyLabel) : await onSubmitBatch(assetIds, applyLabel);
      ids.forEach((id) => trackExisting(id, title, "photo", applyLabel));
      setSubmittedIds(ids);
    } catch (err) {
      setError(errorText(err));
    }
  };

  const generateDisabled =
    submitting ||
    uploadImg.isPending ||
    !extraValid ||
    (mode === "single" ? !source.value : batchItems.length === 0);

  // 提交的 task 实时状态(从 tasks-context)。单个失败不连累其他(各自 ResultTile)。
  const resultTasks = submittedIds
    .map((id) => tasks.find((t) => t.taskId === id))
    .filter((t): t is TrackedTask => Boolean(t));
  const downloadable = resultTasks.filter((t) => t.status === "done" && (t.downloadUrl ?? t.playbackUrl));

  // 批量下载防连点：ref 同步锁→一次只触发一批；1.2s 后复位（disabled 提供视觉反馈）。
  const onDownloadAll = () => {
    if (downloadingAllRef.current) return;
    downloadingAllRef.current = true;
    setDownloadingAll(true);
    downloadable.forEach((t) => triggerDownload(t.downloadUrl ?? (t.playbackUrl as string)));
    downloadTimerRef.current = setTimeout(() => {
      downloadingAllRef.current = false;
      setDownloadingAll(false);
    }, 1200);
  };

  return (
    <Card animateIn>
      <CardTitle>{title}</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">{subtitle}</CardSubtitle>

      {/* 单张 / 批量 toggle */}
      <div className="mb-[15px]">
        <div role="group" aria-label={copy.workbench.ecomModeGroupLabel} className="grid grid-cols-2 gap-2">
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
          inputId={`${idPrefix}-source`}
        />
      ) : (
        <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
          <legend className={labelClass}>{copy.workbench.ecomUploadBatchLabel}</legend>
          <input
            ref={batchInputRef}
            id={`${idPrefix}-batch`}
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

      {/* 工具专属偏好控件（背景 / 性别+风格+补充） */}
      {children}

      {complianceHint && <p className="mb-3 text-[12px] text-ink-faint">{complianceHint}</p>}

      <AiLabelToggle checked={applyLabel} onChange={setApplyLabel} />

      {error && (
        <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      <Button variant="primary" size="lg" className="mt-1 w-full" onClick={() => void onGenerate()} disabled={generateDisabled}>
        {submitting ? <RefreshCw size={18} strokeWidth={1.8} className="animate-spin" /> : icon}
        {submitting ? copy.workbench.ecomGenerating : copy.workbench.ecomGenerate}
      </Button>

      {resultTasks.length > 0 && (
        <div className="mt-5 border-t border-line-gold pt-5">
          <div className="mb-3 flex items-center justify-between">
            <span className={labelClass + " mb-0"}>{copy.workbench.ecomResultsLabel}</span>
            {submittedIds.length > 1 && downloadable.length > 0 && (
              <Button variant="soft" size="sm" onClick={onDownloadAll} disabled={downloadingAll}>
                <Download size={14} strokeWidth={2} /> {copy.workbench.ecomDownloadAll}
              </Button>
            )}
          </div>
          {/* 异步轮询结果区做 live region：瓦片 排队→进行→完成/失败 的状态变更对屏幕阅读器礼貌播报
              (对齐 cover-panel)；失败瓦片自身 role=alert 即时播报，故 aria-atomic=false 只播报变更项。 */}
          <div role="status" aria-live="polite" aria-atomic="false">
            {submittedIds.length === 1 ? (
              <ResultTile task={resultTasks[0]} decoration={resultDecoration} />
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {resultTasks.map((t) => (
                  <ResultTile key={t.taskId} task={t} decoration={resultDecoration} />
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
