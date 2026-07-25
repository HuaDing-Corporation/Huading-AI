"use client";

import { useEffect, useState } from "react";
import { Download, Loader2, Plus, RotateCw, Sparkles, Trash2 } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import {
  confirmEcomReplicate,
  ecomReplicateActualDimensions,
  ECOM_REPLICATE_MAX_IMAGES,
  ECOM_REPLICATE_MAX_POINTS,
  ECOM_REPLICATE_REF_MAX,
  getEcomReplicateJob,
  isEcomReplicateSettled,
  planEcomReplicate,
  retryEcomReplicateOutput,
  type EcomReplicateJob,
  type EcomReplicateMode,
  type EcomReplicatePlanOutput
} from "@/lib/api/ecom-replicate";
import { Button } from "@/components/ui/button";
import { ElapsedSince } from "@/components/common/elapsed-since";
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

/** 提示词摘要（规划表 v1 用 theme + 摘要代替臆造的主/副标题列）。 */
function promptSummary(prompt?: string | null): string {
  const t = (prompt ?? "").trim();
  if (!t) return "—";
  return t.length > 48 ? `${t.slice(0, 48)}…` : t;
}

/** 结果单张：预览仅 CSS 等比缩放；下载给原图 bytes（<a download>，**零 canvas/crop/resize**，红线安全）；不隐藏原始尺寸。 */
function ResultTile({
  output,
  mode,
  retrying,
  retryBusy,
  onRetry
}: {
  output: EcomReplicatePlanOutput;
  mode: EcomReplicateMode;
  retrying: boolean;
  /** 有任一张正在重试（串行化）→ 全部重试按钮禁用，避免点了没反应的静默无反馈。 */
  retryBusy: boolean;
  onRetry: () => void;
}) {
  const pageNo = output.index + 1;
  // 原图尺寸透明红线：只在后端真回 actual_width/height 时展示「AI 原始输出尺寸」；缺失（null）绝不把请求尺寸冒充实际输出。
  const actual = ecomReplicateActualDimensions(output);
  const sizeHint = actual
    ? mode === "main"
      ? copy.workbench.ecomResultSizeMain(actual)
      : copy.workbench.ecomResultSizeDetail(actual)
    : copy.workbench.ecomResultSizeUnknown;
  const failed = output.status === "failed";
  return (
    <div className="flex flex-col gap-2 rounded-field border border-line-gold bg-glass-fill p-2.5">
      <div className="flex items-center justify-between text-[12px] text-ink-soft">
        <span>{copy.workbench.ecomResultPageNo(pageNo)}</span>
        <span className="text-ink-faint">{copy.workbench.ecomReplicateTheme(output.theme)}</span>
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
          {/* 真 BE 唯一图片 URL 即 download_url（无 preview_url）：预览仅 CSS 等比缩放该原图（object-contain，零裁剪）。 */}
          {output.download_url ? (
            <>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={output.download_url}
                alt={copy.workbench.ecomResultPreviewAlt(pageNo)}
                className="w-full rounded-mark border border-line-gold object-contain"
              />
              {/* 不隐藏原始尺寸 + 平台建议 + 未裁剪提示（§18） */}
              <p className="text-[11.5px] leading-relaxed text-ink-faint">{sizeHint}</p>
              {/* 下载 = 浏览器按同一原图 URL 取 bytes；download 属性，无任何前端后处理（零 canvas/crop/resize）。 */}
              <a
                href={output.download_url}
                download={`ecom-replicate-${pageNo}.png`}
                className="inline-flex h-9 items-center justify-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 text-[13px] text-ink-soft outline-none transition-colors hover:bg-white/60 focus-visible:shadow-focus-gold"
              >
                <Download size={14} strokeWidth={2} /> {copy.workbench.ecomResultDownload}
              </a>
            </>
          ) : (
            // download_url 缺失（防御）：不渲染无 src 空图/死链，给尺寸信息 + 明确禁用态。
            <>
              <p className="text-[11.5px] leading-relaxed text-ink-faint">{sizeHint}</p>
              <span
                aria-disabled="true"
                className="inline-flex h-9 cursor-not-allowed items-center justify-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 text-[13px] text-ink-faint opacity-60"
              >
                <Download size={14} strokeWidth={2} /> {copy.workbench.ecomResultDownloadUnavailable}
              </span>
            </>
          )}
        </>
      )}
    </div>
  );
}

/**
 * 电商详情图·强制复刻向导（ECOM-REPLICATE-UI-0001 · FIX1 对齐真实 BE 契约）。4 Step 状态机：
 * 上传(模式二选一 + 参考图 主图1–5/详情1–12 · 商品图1–4/商品信息 dict/卖点≤8；超限显式拦截不静默丢图) → 规划表确认(渲染 plan.outputs + total_credits
 * 后端取 + 扣费 ConfirmDialog 恰一次) → 生成中(轮询 GET、禁分批、全部完成才展示) → 结果(一次性 + 原始尺寸
 * actual_w/h §18 + 下载原图 + 单张按 index 重试不二次扣)。契约走 lib/api/ecom-replicate。
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
  const [retryingIndex, setRetryingIndex] = useState<number | null>(null);
  const [pollError, setPollError] = useState(false); // 轮询瞬时失败 → 软提示（不放弃整套）
  const [pollTick, setPollTick] = useState(0); // 失败后自增以重排下一次轮询（job 未变，靠此触发 effect 重跑）
  // 诚实计时的基准（GEN-HEARTBEAT-UI-0001 · FIX1 通道②）：本链路**不走 tasks-context 看门狗**（它是独立轮询），
  // 故自己记「进入生成态的时刻」= 用户按下确认扣费、任务真正开始的那一刻。
  const [generatingSince, setGeneratingSince] = useState<number | null>(null);

  // 计时基准起算：**每次进入生成态**都重新起算（确认扣费进来是第一轮；单张重试再进来是新一轮 →
  // 报"本轮已等多久"，不累加上一轮，否则重试刚开始 5 秒却显示"已 20 分钟"）。
  // 用 effect 而不是在两个 handler 里各写一次：onConfirmCharge 与 onRetry 都会置 generating，
  // effect 是它们唯一的汇合点（少一处要人去同步的地方）。附带一提，本仓 eslint 的 react-hooks/purity
  // 实测会拦下直接写在 onRetry 里的 `Date.now()`（同样的调用在 useCallback 里则不拦）。
  useEffect(() => {
    if (step === "generating") setGeneratingSince(Date.now());
  }, [step]);

  // 生成中轮询 GET /replicate/{id}：job 更新驱动重跑；settled 才切结果（**全部完成才一次性展示**，生成阶段只显进度）。
  // 红线：轮询瞬时失败（500/离线）**绝不**跳结果页把未完成输出当成品展示（用户已扣费、后端仍在生成）——保持生成中、
  // 软提示、下一拍自动重试（transient 可自愈），结果页只经 settled-gate 到达。
  useEffect(() => {
    if (step !== "generating" || !job) return;
    if (isEcomReplicateSettled(job.status)) {
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
  const canAddPoint = points.length < ECOM_REPLICATE_MAX_POINTS;
  // 参考图上限随模式（主图 5 / 详情 12）；mode 未选时用较大值(detail)，实际拦截以选定模式为准。商品图恒 4。
  const refMax = ECOM_REPLICATE_REF_MAX[mode ?? "detail"];
  const validateUpload = (): string | null => {
    if (!mode) return copy.errors.ecomDetailNeedMode;
    if (refIds.length < 1) return copy.errors.ecomDetailNeedRef;
    // ECOM-REF-LIMIT-UI-0001：切模式后超上限 → 明确拦截让用户删减，**绝不 slice 静默丢图**（口径对齐 BE 422）。
    if (refIds.length > ECOM_REPLICATE_REF_MAX[mode]) {
      return copy.errors.ecomDetailRefOverLimit(ECOM_REPLICATE_REF_MAX[mode]);
    }
    if (productIds.length < 1) return copy.errors.ecomDetailNeedProduct;
    if (productIds.length > ECOM_REPLICATE_MAX_IMAGES) {
      return copy.errors.ecomDetailProductOverLimit(ECOM_REPLICATE_MAX_IMAGES); // 商品图专属文案（防御，正常 picker 已封顶 4，不随模式变）
    }
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
        // 不再 slice 静默截断：越限已在 validateUpload 显式拦截；此处发真实数量（对齐 BE 上限校验）。
        reference_image_asset_ids: refIds,
        product_image_asset_ids: productIds,
        // 真契约：product_info 为 dict（非字符串）。单一「商品信息」自由文本落 description 键。
        product_info: { description: productInfo.trim() },
        selling_points: cleanPoints.slice(0, ECOM_REPLICATE_MAX_POINTS)
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
      // confirm 只回 minimal（无 outputs）——**不覆盖**已有 plan.outputs；置 generating 进入轮询，
      // 由 GET 取真实逐张状态（即便 BE 已 completed，也先 GET 一次拿新 outputs，避免展示 planned 陈图）。
      setJob((prev) =>
        prev ? { ...prev, status: "generating", output_count: confirmed.output_count, total_credits: confirmed.total_credits } : prev
      );
      setPollError(false);
      setChargeOpen(false);
      setStep("generating");
    } catch (err) {
      setChargeOpen(false);
      setError(friendly(err, copy.errors.ecomDetailGenFailed));
    } finally {
      setConfirming(false);
    }
  };

  const onRetry = async (index: number) => {
    if (!job || retryingIndex !== null) return;
    setRetryingIndex(index);
    try {
      const retried = await retryEcomReplicateOutput(job.job_id, index);
      // 单张重试：BE 使该张回 planned + 整单回 generating（**不重复扣费**）。本地并入该张最新态、置 generating
      // 回到轮询，等其重新出图后整套一次性刷新（与「禁分批/全部完成才展示」一致）。
      setJob((prev) =>
        prev
          ? {
              ...prev,
              status: "generating",
              plan: { ...prev.plan, outputs: prev.plan.outputs.map((o) => (o.index === index ? { ...o, ...retried } : o)) }
            }
          : prev
      );
      setPollError(false);
      setStep("generating");
    } catch (err) {
      setError(friendly(err, copy.errors.ecomDetailGenFailed));
    } finally {
      setRetryingIndex(null);
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

        {/* 参考图上限随模式（主图 5 / 详情 12）——超限在 validateUpload 显式拦截，不静默截断 */}
        <ReferenceImagesPicker
          inputId="ecom-detail-ref"
          label={copy.workbench.ecomDetailRefLabel(refMax)}
          uploadLabel={copy.workbench.ecomDetailRefUpload}
          overLimitError={copy.workbench.refImagesOverLimit(refMax)}
          max={refMax}
          onChange={setRefIds}
        />
        {/* 商品图恒 ≤4（不随模式变） */}
        <ReferenceImagesPicker
          inputId="ecom-detail-product"
          label={copy.workbench.ecomDetailProductLabel(ECOM_REPLICATE_MAX_IMAGES)}
          uploadLabel={copy.workbench.ecomDetailProductUpload}
          overLimitError={copy.workbench.refImagesOverLimit(ECOM_REPLICATE_MAX_IMAGES)}
          max={ECOM_REPLICATE_MAX_IMAGES}
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

        {/* 核心卖点（多条，可增删，≤8 条） */}
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
          {canAddPoint && (
            <button
              type="button"
              onClick={() => setPoints((prev) => (prev.length < ECOM_REPLICATE_MAX_POINTS ? [...prev, ""] : prev))}
              className="mt-2 inline-flex items-center gap-1 rounded-field px-2 py-1 text-[12.5px] text-gold-deep outline-none hover:bg-glass-soft focus-visible:shadow-focus-gold"
            >
              <Plus size={14} strokeWidth={2} /> {copy.workbench.ecomDetailAddPoint}
            </button>
          )}
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
    return (
      <Card animateIn>
        <CardTitle>{copy.workbench.ecomPlanTitle}</CardTitle>

        <div className="mb-3 overflow-x-auto rounded-field border border-line-gold">
          <table className="w-full min-w-[560px] border-collapse text-left text-[12px]">
            <thead>
              <tr className="bg-glass-soft text-ink-soft">
                <th className="px-2 py-1.5 font-medium">{copy.workbench.ecomPlanColPage}</th>
                <th className="px-2 py-1.5 font-medium">{copy.workbench.ecomPlanColTheme}</th>
                <th className="px-2 py-1.5 font-medium">{copy.workbench.ecomPlanColSize}</th>
                <th className="px-2 py-1.5 font-medium">{copy.workbench.ecomPlanColPrompt}</th>
                <th className="px-2 py-1.5 font-medium">{copy.workbench.ecomPlanColOutput}</th>
              </tr>
            </thead>
            <tbody>
              {job.plan.outputs.map((o) => (
                <tr key={o.id} className="border-t border-line-gold text-ink">
                  <td className="px-2 py-1.5">{o.index + 1}</td>
                  <td className="px-2 py-1.5">{copy.workbench.ecomReplicateTheme(o.theme)}</td>
                  <td className="px-2 py-1.5">{o.requested_size}</td>
                  <td className="px-2 py-1.5 text-ink-soft" title={o.prompt ?? undefined}>
                    {promptSummary(o.prompt)}
                  </td>
                  <td className="px-2 py-1.5 text-ink-faint">{copy.workbench.ecomPlanNoCrop}</td>
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
    const done = job.plan.outputs.filter((o) => o.status === "succeeded" || o.status === "failed").length;
    const total = job.output_count || job.plan.outputs.length;
    return (
      <Card animateIn>
        <CardTitle>{copy.workbench.ecomGenTitle}</CardTitle>
        <div className="mt-4 flex flex-col items-center gap-3 py-6 text-center" role="status" aria-live="polite">
          <Loader2 size={28} className="animate-spin text-gold-deep" />
          <p className="text-[13px] text-ink">{copy.workbench.ecomGenProgress(done, total)}</p>
          <p className="text-[12px] text-ink-faint">{copy.workbench.ecomGenWait}</p>
          {/* GEN-HEARTBEAT-UI-0001 · FIX1 通道②：轮询响应带 heartbeat_at → 补一行诚实计时（与图片生成同一份实现）。
              🔴 heartbeat_at 只当**布尔证据**用（BE 在 Redis 不可用时降级为 null，业务轮询不受影响）：
              null / 缺省 → 这一行不渲染，**其余等待态原样保留**，不出 NaN、不出 Invalid Date、更不判失败。 */}
          {job.heartbeat_at != null && generatingSince != null ? <ElapsedSince startedAt={generatingSince} /> : null}
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
    const outputs = job.plan.outputs;
    // 整单失败（status=failed，通常无任何输出）→ 明确告知 + 返回入口，不渲染空网格/坏图。
    const allFailed = job.status === "failed" || outputs.length === 0;
    const retryBusy = retryingIndex !== null;
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
            {outputs.map((o) => (
              <ResultTile
                key={o.id}
                output={o}
                mode={job.output_mode}
                retrying={retryingIndex === o.index}
                retryBusy={retryBusy}
                onRetry={() => void onRetry(o.index)}
              />
            ))}
          </div>
        )}
      </Card>
    );
  }

  return null;
}
