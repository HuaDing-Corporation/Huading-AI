"use client";

import { useEffect, useState } from "react";
import { Download, Loader2, Plus, RotateCw, Sparkles, Trash2 } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import {
  confirmEcomReplicate,
  getEcomReplicateJob,
  isEcomReplicateSettled,
  planEcomReplicate,
  retryEcomReplicateOutput,
  type EcomReplicateJob,
  type EcomReplicateMode,
  type EcomReplicateOutput
} from "@/lib/api/ecom-replicate";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { SelectableOption } from "@/components/ui/selectable-option";
import { ReferenceImagesPicker } from "@/components/workbench/reference-images-picker";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
type Step = "upload" | "plan" | "generating" | "result";

function friendly(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message || fallback : fallback;
}

/** 结果单张：预览仅 CSS 等比缩放；下载给原图 bytes（<a download>，**零 canvas/crop/resize**，红线安全）；不隐藏原始尺寸。 */
function ResultTile({
  output,
  mode,
  retrying,
  retryBusy,
  onRetry
}: {
  output: EcomReplicateOutput;
  mode: EcomReplicateMode;
  retrying: boolean;
  /** 有任一张正在重试（串行化）→ 全部重试按钮禁用，避免点了没反应的静默无反馈。 */
  retryBusy: boolean;
  onRetry: () => void;
}) {
  // 原图尺寸透明红线：只在后端真回 actual_dimensions 时展示「AI 原始输出尺寸」；缺失（null）绝不把请求尺寸冒充实际输出。
  const sizeHint = output.actual_dimensions
    ? mode === "main"
      ? copy.workbench.ecomResultSizeMain(output.actual_dimensions)
      : copy.workbench.ecomResultSizeDetail(output.actual_dimensions)
    : copy.workbench.ecomResultSizeUnknown;
  const failed = output.status === "failed";
  return (
    <div className="flex flex-col gap-2 rounded-field border border-line-gold bg-glass-fill p-2.5">
      <div className="flex items-center justify-between text-[12px] text-ink-soft">
        <span>{copy.workbench.ecomResultPageNo(output.page_no)}</span>
      </div>
      {failed ? (
        <div role="alert" className="flex flex-col items-center gap-2 rounded-mark bg-error-bg px-3 py-6 text-center text-[12.5px] text-error-fg">
          <span>{copy.workbench.ecomResultFailed}</span>
          <Button variant="soft" size="sm" onClick={onRetry} disabled={retrying || retryBusy}>
            <RotateCw size={13} strokeWidth={2} /> {retrying ? copy.workbench.ecomResultRetrying : copy.workbench.ecomResultRetry}
          </Button>
        </div>
      ) : (
        <>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={output.preview_url ?? undefined}
            alt={copy.workbench.ecomResultPreviewAlt(output.page_no)}
            className="w-full rounded-mark border border-line-gold object-contain"
          />
          {/* 不隐藏原始尺寸 + 平台建议 + 未裁剪提示（§18） */}
          <p className="text-[11.5px] leading-relaxed text-ink-faint">{sizeHint}</p>
          {/* 下载 = 浏览器按 URL 取原始 bytes；download 属性，无任何前端后处理。download_url 缺失时给禁用态而非死链。 */}
          {output.download_url ? (
            <a
              href={output.download_url}
              download={`ecom-detail-${output.page_no}.png`}
              className="inline-flex h-9 items-center justify-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 text-[13px] text-ink-soft outline-none transition-colors hover:bg-white/60 focus-visible:shadow-focus-gold"
            >
              <Download size={14} strokeWidth={2} /> {copy.workbench.ecomResultDownload}
            </a>
          ) : (
            <span
              aria-disabled="true"
              className="inline-flex h-9 cursor-not-allowed items-center justify-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 text-[13px] text-ink-faint opacity-60"
            >
              <Download size={14} strokeWidth={2} /> {copy.workbench.ecomResultDownloadUnavailable}
            </span>
          )}
        </>
      )}
    </div>
  );
}

/**
 * 电商详情图·强制复刻向导（ECOM-REPLICATE-UI-0001）。4 Step 状态机：
 * 上传(模式二选一 + 参考图1+/商品图1+/商品信息/卖点多条) → 规划表确认(渲染 §10 表 + total_credits 后端取 +
 * 扣费 ConfirmDialog 恰一次) → 生成中(轮询、禁分批、全部完成才展示) → 结果(一次性 + 原始尺寸 §18 + 下载原图 +
 * 单张重试不二次扣)。契约走 lib/api/ecom-replicate（mock 先行，以 BE 包为准）。
 */
export function EcomDetailWizard() {
  const [step, setStep] = useState<Step>("upload");
  const [mode, setMode] = useState<EcomReplicateMode | null>(null);
  const [refIds, setRefIds] = useState<string[]>([]);
  const [productIds, setProductIds] = useState<string[]>([]);
  const [productInfo, setProductInfo] = useState("");
  const [points, setPoints] = useState<string[]>([""]);
  const [job, setJob] = useState<EcomReplicateJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [chargeOpen, setChargeOpen] = useState(false);
  const [retryingPage, setRetryingPage] = useState<number | null>(null);
  const [pollError, setPollError] = useState(false); // 轮询瞬时失败 → 软提示（不放弃整套）
  const [pollTick, setPollTick] = useState(0); // 失败后自增以重排下一次轮询（job 未变，靠此触发 effect 重跑）

  // 生成中轮询：job 更新驱动重跑；settled 才切结果（**全部完成才一次性展示**，生成阶段只显进度）。
  // 红线：轮询瞬时失败（500/离线）**绝不**跳结果页把未完成的 pending 输出当成品展示（用户已扣费、后端仍在
  // 生成）——保持生成中、软提示、下一拍自动重试（transient 可自愈），结果页只经 settled-gate 到达。
  useEffect(() => {
    if (step !== "generating" || !job) return;
    if (isEcomReplicateSettled(job)) {
      setStep("result");
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const next = await getEcomReplicateJob(job.job_id);
        if (!cancelled) {
          setPollError(false);
          setJob(next);
        }
      } catch {
        if (!cancelled) {
          setPollError(true);
          setPollTick((t) => t + 1); // 重排下一拍轮询，继续等待整套完成
        }
      }
    }, 1500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [step, job, pollTick]);

  const cleanPoints = points.map((p) => p.trim()).filter(Boolean);
  const validateUpload = (): string | null => {
    if (!mode) return copy.errors.ecomDetailNeedMode;
    if (refIds.length < 1) return copy.errors.ecomDetailNeedRef;
    if (productIds.length < 1) return copy.errors.ecomDetailNeedProduct;
    if (!productInfo.trim()) return copy.errors.ecomDetailNeedInfo;
    if (cleanPoints.length < 1) return copy.errors.ecomDetailNeedPoint;
    return null;
  };

  const onPlan = async () => {
    setError(null);
    const invalid = validateUpload();
    if (invalid) {
      setError(invalid);
      return;
    }
    setPlanning(true);
    try {
      const planned = await planEcomReplicate({
        output_mode: mode as EcomReplicateMode,
        reference_image_asset_ids: refIds,
        product_image_asset_ids: productIds,
        product_info: productInfo.trim(),
        selling_points: cleanPoints
      });
      setJob(planned);
      setStep("plan");
    } catch (err) {
      setError(friendly(err, copy.errors.ecomDetailFailed));
    } finally {
      setPlanning(false);
    }
  };

  const onConfirmCharge = async () => {
    if (!job || confirming) return;
    setConfirming(true);
    try {
      const confirmed = await confirmEcomReplicate(job.job_id);
      setJob(confirmed);
      setChargeOpen(false);
      setStep("generating");
    } catch (err) {
      setChargeOpen(false);
      setError(friendly(err, copy.errors.ecomDetailGenFailed));
    } finally {
      setConfirming(false);
    }
  };

  const onRetry = async (pageNo: number) => {
    if (!job || retryingPage !== null) return;
    setRetryingPage(pageNo);
    try {
      const next = await retryEcomReplicateOutput(job.job_id, pageNo);
      setJob(next);
    } catch (err) {
      setError(friendly(err, copy.errors.ecomDetailGenFailed));
    } finally {
      setRetryingPage(null);
    }
  };

  // ── Step: 上传 ──
  if (step === "upload") {
    return (
      <Card animateIn>
        <CardTitle>{copy.workbench.ecomDetailTitle}</CardTitle>
        <CardSubtitle className="mb-[18px] mt-1">{copy.workbench.ecomDetailSubtitle}</CardSubtitle>

        {/* 出图模式二选一（缺 → 提示） */}
        <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
          <legend className={labelClass}>{copy.workbench.ecomDetailModeLabel}</legend>
          <div role="group" aria-label={copy.workbench.ecomDetailModeLabel} className="grid grid-cols-2 gap-2">
            <SelectableOption selected={mode === "main"} onSelect={() => setMode("main")} className="justify-center">
              {copy.workbench.ecomDetailModeMain}
            </SelectableOption>
            <SelectableOption selected={mode === "detail"} onSelect={() => setMode("detail")} className="justify-center">
              {copy.workbench.ecomDetailModeDetail}
            </SelectableOption>
          </div>
        </fieldset>

        <ReferenceImagesPicker
          inputId="ecom-detail-ref"
          label={copy.workbench.ecomDetailRefLabel}
          uploadLabel={copy.workbench.ecomDetailRefUpload}
          overLimitError={copy.workbench.refImagesOverLimit}
          onChange={setRefIds}
        />
        <ReferenceImagesPicker
          inputId="ecom-detail-product"
          label={copy.workbench.ecomDetailProductLabel}
          uploadLabel={copy.workbench.ecomDetailProductUpload}
          overLimitError={copy.workbench.refImagesOverLimit}
          onChange={setProductIds}
        />

        <div className="mb-[15px]">
          <label htmlFor="ecom-detail-info" className={labelClass}>
            {copy.workbench.ecomDetailInfoLabel}
          </label>
          <textarea
            id="ecom-detail-info"
            value={productInfo}
            onChange={(e) => setProductInfo(e.target.value)}
            rows={2}
            placeholder={copy.workbench.ecomDetailInfoPlaceholder}
            className="w-full resize-none rounded-field border border-line-gold bg-glass-fill px-3 py-2 text-[13px] text-ink outline-none focus-visible:shadow-focus-gold"
          />
        </div>

        {/* 核心卖点（多条，可增删） */}
        <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
          <legend className={labelClass}>{copy.workbench.ecomDetailPointsLabel}</legend>
          <div className="flex flex-col gap-2">
            {points.map((pt, i) => (
              <div key={i} className="flex items-center gap-2">
                <Input
                  value={pt}
                  onChange={(e) => setPoints((prev) => prev.map((p, j) => (j === i ? e.target.value : p)))}
                  placeholder={copy.workbench.ecomDetailPointPlaceholder}
                />
                {points.length > 1 && (
                  <button
                    type="button"
                    onClick={() => setPoints((prev) => prev.filter((_, j) => j !== i))}
                    aria-label={copy.workbench.ecomDetailRemovePoint}
                    className="flex h-9 w-9 flex-none items-center justify-center rounded-mark text-ink-soft hover:bg-glass-hover"
                  >
                    <Trash2 size={15} strokeWidth={2} />
                  </button>
                )}
              </div>
            ))}
          </div>
          <button
            type="button"
            onClick={() => setPoints((prev) => [...prev, ""])}
            className="mt-2 inline-flex items-center gap-1 rounded-field px-2 py-1 text-[12.5px] text-gold-deep outline-none hover:bg-glass-soft focus-visible:shadow-focus-gold"
          >
            <Plus size={14} strokeWidth={2} /> {copy.workbench.ecomDetailAddPoint}
          </button>
        </fieldset>

        {error && (
          <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
            {error}
          </p>
        )}

        <Button variant="primary" size="lg" className="w-full" onClick={() => void onPlan()} disabled={planning}>
          {planning ? (
            <>
              <Loader2 size={18} className="animate-spin" /> {copy.workbench.ecomDetailPlanning}
            </>
          ) : (
            <>
              <Sparkles size={18} strokeWidth={1.8} /> {copy.workbench.ecomDetailPlan}
            </>
          )}
        </Button>
      </Card>
    );
  }

  // ── Step: 规划表确认（扣费门） ──
  if (step === "plan" && job) {
    const anyReused = job.plan.some((p) => p.reused);
    return (
      <Card animateIn>
        <CardTitle>{copy.workbench.ecomPlanTitle}</CardTitle>
        {anyReused && <p className="mb-2 mt-1 text-[12px] text-ink-faint">{copy.workbench.ecomPlanReuseHint}</p>}

        <div className="mb-3 overflow-x-auto rounded-field border border-line-gold">
          <table className="w-full min-w-[640px] border-collapse text-left text-[12px]">
            <thead>
              <tr className="bg-glass-soft text-ink-soft">
                <th className="px-2 py-1.5 font-medium">{copy.workbench.ecomPlanColPage}</th>
                <th className="px-2 py-1.5 font-medium">{copy.workbench.ecomPlanColTheme}</th>
                <th className="px-2 py-1.5 font-medium">{copy.workbench.ecomPlanColRef}</th>
                <th className="px-2 py-1.5 font-medium">{copy.workbench.ecomPlanColMainTitle}</th>
                <th className="px-2 py-1.5 font-medium">{copy.workbench.ecomPlanColSubTitle}</th>
                <th className="px-2 py-1.5 font-medium">{copy.workbench.ecomPlanColStyle}</th>
                <th className="px-2 py-1.5 font-medium">{copy.workbench.ecomPlanColSize}</th>
                <th className="px-2 py-1.5 font-medium">{copy.workbench.ecomPlanColOutput}</th>
              </tr>
            </thead>
            <tbody>
              {job.plan.map((p) => (
                <tr key={p.page_no} className="border-t border-line-gold text-ink">
                  <td className="px-2 py-1.5">{p.page_no}</td>
                  <td className="px-2 py-1.5">{p.theme}</td>
                  <td className="px-2 py-1.5">
                    {p.ref_label}
                    {p.reused && <span className="ml-1 rounded-pill bg-chip-sel px-1.5 py-0.5 text-[10px] text-gold-deep">{copy.workbench.ecomPlanReusedBadge}</span>}
                  </td>
                  <td className="px-2 py-1.5 text-ink-soft">{p.main_title || "—"}</td>
                  <td className="px-2 py-1.5 text-ink-soft">{p.sub_title || "—"}</td>
                  <td className="px-2 py-1.5 text-ink-soft">{p.display_style}</td>
                  <td className="px-2 py-1.5">{p.requested_size}</td>
                  <td className="px-2 py-1.5 text-ink-faint">{p.no_crop_notice}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 整套总价：**取后端 total_credits，不前端硬编码** */}
        <p className="mb-3 text-[13px] font-medium text-ink">{copy.workbench.ecomPlanTotalPrice(job.total_credits)}</p>

        {error && (
          <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
            {error}
          </p>
        )}

        <div className="flex gap-2">
          <Button variant="soft" size="lg" className="flex-1" onClick={() => setStep("upload")}>
            {copy.workbench.ecomPlanBack}
          </Button>
          <Button variant="primary" size="lg" className="flex-1" onClick={() => setChargeOpen(true)}>
            {copy.workbench.ecomPlanConfirm}
          </Button>
        </div>

        <ConfirmDialog
          open={chargeOpen}
          title={copy.workbench.ecomChargeTitle}
          message={copy.workbench.ecomChargeMessage(job.total_credits)}
          confirmLabel={copy.workbench.ecomChargeConfirm}
          submitting={confirming}
          onConfirm={() => void onConfirmCharge()}
          onCancel={() => setChargeOpen(false)}
        />
      </Card>
    );
  }

  // ── Step: 生成中（禁分批，只显进度） ──
  if (step === "generating" && job) {
    const done = job.outputs.filter((o) => o.status === "succeeded" || o.status === "failed").length;
    const total = job.outputs.length || job.plan.length;
    return (
      <Card animateIn>
        <CardTitle>{copy.workbench.ecomGenTitle}</CardTitle>
        <div className="mt-4 flex flex-col items-center gap-3 py-6 text-center" role="status" aria-live="polite">
          <Loader2 size={28} className="animate-spin text-gold-deep" />
          <p className="text-[13px] text-ink">{copy.workbench.ecomGenProgress(done, total)}</p>
          <p className="text-[12px] text-ink-faint">{copy.workbench.ecomGenWait}</p>
          <div className="h-1.5 w-full max-w-xs overflow-hidden rounded-pill bg-track">
            <div className="h-full rounded-pill bg-grad-gold transition-[width]" style={{ width: `${total ? Math.round((done / total) * 100) : 0}%` }} />
          </div>
          {/* 轮询瞬时失败：软提示、后台自动重试；不放弃整套、不把未完成图当结果 */}
          {pollError && <p className="text-[12px] text-error-fg">{copy.workbench.ecomGenRetrying}</p>}
        </div>
      </Card>
    );
  }

  // ── Step: 结果（一次性展示） ──
  if (step === "result" && job) {
    // 整单失败（status=failed，通常无任何输出）→ 明确告知 + 返回入口，不渲染空网格/坏图。
    const allFailed = job.status === "failed" || job.outputs.length === 0;
    const retryBusy = retryingPage !== null;
    return (
      <Card animateIn>
        <CardTitle>{copy.workbench.ecomResultTitle}</CardTitle>
        {job.status === "partial_failed" && (
          <p className="mb-2 mt-1 text-[12.5px] text-error-fg">{copy.workbench.ecomResultPartialHint}</p>
        )}
        {error && (
          <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
            {error}
          </p>
        )}
        {allFailed ? (
          <div className="mt-2 flex flex-col items-center gap-3 py-8 text-center">
            <p role="alert" className="rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
              {copy.workbench.ecomResultAllFailed}
            </p>
            <Button variant="soft" size="sm" onClick={() => setStep("upload")}>
              {copy.workbench.ecomPlanBack}
            </Button>
          </div>
        ) : (
          <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {job.outputs.map((o) => (
              <ResultTile
                key={o.page_no}
                output={o}
                mode={job.output_mode}
                retrying={retryingPage === o.page_no}
                retryBusy={retryBusy}
                onRetry={() => void onRetry(o.page_no)}
              />
            ))}
          </div>
        )}
      </Card>
    );
  }

  return null;
}
