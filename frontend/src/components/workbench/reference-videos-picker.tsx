"use client";

import { useEffect, useRef, useState } from "react";
import { Clapperboard, X } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useUploadVideoGenReference } from "@/lib/api/hooks";
import {
  ALLOWED_REFERENCE_VIDEO_TYPES,
  MAX_REFERENCE_VIDEOS,
  MAX_TOTAL_REFERENCE_SEC,
  inspectReferenceVideo,
  totalDurationStatus,
  type ReferenceVideoInspection
} from "@/lib/media/reference-video";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/** 有序参考视频项（assetId + 预览 object-URL + 时长/告知标记）。预览 URL 生命周期由本组件持有。 */
export interface ReferenceVideoItem {
  assetId: string;
  preview: string;
  duration: number; // 秒（预检读到的元数据）
  willTranscode: boolean; // MOV/WEBM → 服务端自动转 MP4（D9 告知）
  willDownscale: boolean; // 短边 >720p → 服务端自动降码（D9 告知）
}

/**
 * 视频生成·参考视频多传（VIDEO-GEN-V2V-UI-0001，D8–D10）。≤3 条（provider 上限）；每条先客户端预检
 * （MP4/MOV/WEBM、≤100MB、单条 ≤15.2s 拒、<480p 拒——inspect 可注入，jsdom 无法解码视频）再上传
 * （POST /uploads/videos?purpose=video_gen_reference → asset_id）；<video> 预览（muted playsInline，非 <img>）；
 * **合计时长 1.8–15.2s 联动**（D10：实时显示合计 + 超限 role=alert 阻断上传，不让用户传完 3 条才被告知）；
 * MOV/WEBM「将自动转 MP4」、>720p「将自动压缩」逐条告知（D9 不静默）；**「不能含真人」显著明示**（D5）。
 * 上抛 {assetId,duration,...} 列表供父级做互斥（D8）与提交 reference_video_asset_ids + 合计门。
 */
export function ReferenceVideosPicker({
  onItemsChange,
  disabled = false,
  disabledHint,
  inputId = "vg-ref-videos",
  inspect = inspectReferenceVideo
}: {
  onItemsChange?: (items: ReferenceVideoItem[]) => void;
  /** D8 互斥：已传参考图时禁用视频上传（hint 说明原因）。 */
  disabled?: boolean;
  disabledHint?: string;
  inputId?: string;
  /** 可注入的预检器（默认完整 inspectReferenceVideo：MIME/大小/时长/分辨率）。测试注入受控结果。 */
  inspect?: (file: File) => Promise<ReferenceVideoInspection>;
}) {
  const upload = useUploadVideoGenReference();
  const [items, setItems] = useState<ReferenceVideoItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    onItemsChange?.(items);
  }, [items, onItemsChange]);

  // 卸载释放预览 object URL（对齐 ReferenceImagesPicker 防泄漏，含在途记账）。
  const itemsRef = useRef(items);
  useEffect(() => {
    itemsRef.current = items;
  }, [items]);
  const pendingPreviews = useRef<Set<string>>(new Set());
  useEffect(
    () => () => {
      itemsRef.current.forEach((it) => URL.revokeObjectURL(it.preview));
      pendingPreviews.current.forEach((url) => URL.revokeObjectURL(url));
    },
    []
  );

  const totalSec = items.reduce((sum, it) => sum + it.duration, 0);
  const totalStatus = totalDurationStatus(totalSec, items.length);

  const onFiles = async (files: FileList | null) => {
    if (!files) return;
    setError(null);
    const all = Array.from(files);
    // 条数上限（D10）：超出部分不上传并明确提示（不静默截断）。
    const room = MAX_REFERENCE_VIDEOS - itemsRef.current.length;
    if (all.length > room) setError(copy.workbench.vgRefVideoOverCount);
    const toProcess = all.slice(0, Math.max(0, room));
    setBusy(true);
    // FIX2（CB P1 · 闭包时序）：itemsRef 只在 render 后由 effect 更新——同批多选时每轮重读 ref 都是旧值
    // （两个 8s 都按 0+8 过闸 → 双双上传，违反 D10「上传前拦」）。改为**本次调用内的局部累计**：初值取一次
    // ref，此后每接受一条就地累加——同批第 N 条的闸能看到前 N-1 条，不依赖 ref/state 的异步更新。
    let batchTotal = itemsRef.current.reduce((s, it) => s + it.duration, 0);
    try {
      for (const file of toProcess) {
        const inspection = await inspect(file);
        if (inspection.error || !inspection.meta) {
          setError(inspection.error ?? copy.errors.videoUnreadable);
          continue; // 单条拒绝：明确提示，继续处理后续文件
        }
        // D10 联动前置闸：这条加上会让合计达到/超过 15.2s → 不上传、直接告知（**不让用户传完才被告知**）。
        // FIX1：>= 对齐 BE 开区间（合计恰 15.2 也 422，routes/videos.py:936-943）。
        const nextTotal = batchTotal + inspection.meta.duration;
        if (nextTotal >= MAX_TOTAL_REFERENCE_SEC) {
          setError(copy.workbench.vgRefVideoTotalOver);
          continue;
        }
        const preview = URL.createObjectURL(file);
        pendingPreviews.current.add(preview);
        try {
          const { asset_id } = await upload.mutateAsync(file);
          batchTotal = nextTotal; // FIX2：接受即累加到本批局部值（上传失败走 catch 不累加），供同批下一条的闸使用
          setItems((prev) => {
            if (prev.length >= MAX_REFERENCE_VIDEOS) {
              URL.revokeObjectURL(preview);
              return prev;
            }
            return [
              ...prev,
              {
                assetId: asset_id,
                preview,
                duration: inspection.meta!.duration,
                willTranscode: inspection.willTranscode,
                willDownscale: inspection.willDownscale
              }
            ];
          });
        } catch (err) {
          URL.revokeObjectURL(preview);
          setError(errorText(err));
        } finally {
          pendingPreviews.current.delete(preview);
        }
      }
    } finally {
      setBusy(false);
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

  const full = items.length >= MAX_REFERENCE_VIDEOS;
  const fmt = (n: number) => n.toFixed(1); // toFixed 本身按 1 位小数舍入（Code Review：去掉冗余的 Math.round 双取整）

  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{copy.workbench.vgRefVideosLabel}</legend>
      {/* D5 真人限制：上传区显著位置明示（非折叠、非 hover） */}
      <p className="mb-2 rounded-field bg-glass-fill px-3 py-2 text-[12px] text-ink-soft">
        {copy.workbench.vgRefVideoNoHuman}
      </p>
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        multiple
        accept={ALLOWED_REFERENCE_VIDEO_TYPES.join(",")}
        className="hidden"
        onChange={(e) => void onFiles(e.target.files)}
      />
      {items.length > 0 && (
        <div className="mb-2.5 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {items.map((item, i) => (
            <div key={item.assetId} className="relative overflow-hidden rounded-mark border border-line-gold">
              {/* 视频预览：muted + playsInline 首帧展示（非 <img>，冻结文档·上传侧障碍③） */}
              <video src={item.preview} muted playsInline preload="metadata" className="aspect-video w-full object-cover" />
              <span className="absolute bottom-0.5 left-0.5 rounded-mark bg-ink/60 px-1.5 py-0.5 text-[11px] text-white">
                {copy.workbench.vgRefVideoSeconds(fmt(item.duration))}
              </span>
              <button
                type="button"
                onClick={() => removeItem(i)}
                aria-label={copy.workbench.removeVideo}
                className="absolute right-0.5 top-0.5 flex h-6 w-6 items-center justify-center rounded-mark bg-ink/50 text-white hover:bg-ink/70"
              >
                <X size={13} strokeWidth={2} />
              </button>
              {/* D9 告知：转码/降码不静默 */}
              {(item.willTranscode || item.willDownscale) && (
                <span className="absolute left-0.5 top-0.5 rounded-mark bg-ink/60 px-1.5 py-0.5 text-[10.5px] text-white">
                  {[item.willTranscode ? copy.workbench.vgRefVideoWillTranscode : null, item.willDownscale ? copy.workbench.vgRefVideoWillDownscale : null]
                    .filter(Boolean)
                    .join("·")}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled || busy || full}
        className="flex w-full items-center justify-center gap-2 rounded-field border border-dashed border-line-gold bg-glass-fill py-5 text-[13px] text-ink-soft transition-colors hover:bg-glass-hover disabled:pointer-events-none disabled:opacity-50"
      >
        <Clapperboard size={18} strokeWidth={1.8} />{" "}
        {busy ? copy.workbench.vgGenerating : `${copy.workbench.vgRefVideosUpload}（${items.length}/${MAX_REFERENCE_VIDEOS}）`}
      </button>
      {/* D8 互斥说明（已传参考图时） */}
      {disabled && disabledHint && <p className="mt-2 text-[12px] text-ink-faint">{disabledHint}</p>}
      {/* D10 合计时长联动：有视频即实时显示；不足/超限转警示色 + live 播报（aria-live 供变化播报） */}
      {items.length > 0 && (
        <p
          aria-live="polite"
          className={cn("mt-2 text-[12.5px]", totalStatus === "ok" ? "text-ink-soft" : "font-medium text-error-fg")}
        >
          {copy.workbench.vgRefVideoTotal(fmt(totalSec))}
          {totalStatus === "low" && ` ${copy.workbench.vgRefVideoTotalLow}`}
          {totalStatus === "over" && ` ${copy.workbench.vgRefVideoTotalOver}`}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
          {error}
        </p>
      )}
    </fieldset>
  );
}
