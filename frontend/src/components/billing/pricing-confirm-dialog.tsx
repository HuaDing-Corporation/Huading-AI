"use client";

import { useEffect } from "react";
import { Clock3, Loader2, ReceiptText, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { BillingStatus } from "@/components/billing/billing-status";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import type { BillingQuote, BillingSummary } from "@/lib/api/types";
import type { BillingActionPhase } from "@/lib/billing/use-billing-action";

export interface PricingConfirmDialogProps {
  open: boolean;
  phase: BillingActionPhase;
  quote: BillingQuote | null;
  expiresInSeconds: number | null;
  errorMessage?: string | null;
  billing?: BillingSummary | null;
  billingQuerying?: boolean;
  onContinueLookup?: () => void;
  onEstimate: () => void;
  onConfirm: () => void;
  onCancel: () => void;
  confirmLabel?: string;
}

export function PricingConfirmDialog({
  open,
  phase,
  quote,
  expiresInSeconds,
  errorMessage,
  billing = null,
  billingQuerying = false,
  onContinueLookup,
  onEstimate,
  onConfirm,
  onCancel,
  confirmLabel = "确认并继续"
}: PricingConfirmDialogProps) {
  useEffect(() => {
    if (open && phase === "idle") onEstimate();
  }, [onEstimate, open, phase]);

  const estimating = phase === "estimating";
  const submitting = phase === "submitting";
  const expired = phase === "expired" || expiresInSeconds === 0;
  const estimateFailed = phase === "failed" && quote === null;
  const confirmable = phase === "ready" && quote !== null && !expired;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !submitting) onCancel();
      }}
    >
      <DialogContent className="flex max-h-[90vh] min-w-0 flex-col gap-5 overflow-y-auto p-5 sm:p-6">
        <div className="min-w-0">
          <DialogTitle className="text-[18px] font-semibold tracking-[.4px] text-ink">
            确认价格并继续
          </DialogTitle>
          <DialogDescription className="mt-1.5 text-[13px] leading-5 text-ink-soft">
            请核对应付积分和计费明细。确认后将按本次服务端报价提交。
          </DialogDescription>
        </div>

        {(billing || billingQuerying) && (
          <BillingStatus
            summary={billing}
            querying={billingQuerying}
            onContinueLookup={onContinueLookup}
          />
        )}

        <div
          data-testid="billing-price-layout"
          className="min-w-0 overflow-hidden rounded-field border border-line-gold bg-white/45"
          aria-live="polite"
        >
          {estimating ? (
            <div className="flex min-h-36 items-center justify-center gap-2 px-4 text-sm text-ink-soft">
              <Loader2 aria-hidden size={17} className="animate-spin" />
              正在获取价格…
            </div>
          ) : estimateFailed ? (
            <div className="flex min-h-36 items-center justify-center gap-2 px-4 text-center text-sm text-error-fg">
              <TriangleAlert aria-hidden size={17} className="flex-none" />
              暂时无法获取价格，请稍后重试
            </div>
          ) : quote ? (
            <div className="min-w-0 divide-y divide-line-gold/70">
              <div className="min-w-0 px-4 py-3.5">
                <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold text-ink">
                  <ReceiptText aria-hidden size={16} className="text-gold-deep" />
                  计费明细
                </div>
                {quote.pricing_shape === "simple" ? (
                  <dl className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-2 text-[13px]">
                    <dt className="text-ink-soft">计费数量</dt>
                    <dd className="break-all text-right font-medium text-ink">
                      {quote.quantity} {quote.unit}
                    </dd>
                    <dt className="text-ink-soft">服务端单价</dt>
                    <dd className="break-all text-right font-medium text-ink">
                      {quote.unit_credits} 积分 / {quote.unit}
                    </dd>
                  </dl>
                ) : (
                  <ul className="min-w-0 space-y-3" aria-label="计费项目">
                    {quote.breakdown.map((line, index) => (
                      <li
                        key={`${line.operation}-${line.capability}-${index}`}
                        className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 text-[13px]"
                      >
                        <span className="min-w-0 break-words font-medium text-ink">{line.label}</span>
                        <span className="break-all text-right font-semibold text-ink">
                          {line.subtotal_credits} 积分
                        </span>
                        <span className="col-span-2 min-w-0 break-words text-[12px] text-ink-faint">
                          {line.quantity} {line.unit} · {line.unit_credits} 积分 / {line.unit}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <dl className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 px-4 py-3 text-[13px]">
                <dt className="text-ink-soft">价格小计</dt>
                <dd className="break-all text-right font-medium text-ink">{quote.subtotal_credits} 积分</dd>
              </dl>

              <div className="flex min-w-0 items-end justify-between gap-4 bg-gold/10 px-4 py-4">
                <span className="text-sm font-semibold text-ink">服务端应付积分</span>
                <span className="break-all text-right text-2xl font-bold tabular-nums text-gold-deep">
                  {quote.payable_credits} 积分
                </span>
              </div>
            </div>
          ) : (
            <div className="min-h-36 px-4 py-6 text-center text-sm text-ink-soft">等待获取价格</div>
          )}
        </div>

        {quote && (
          <div className="space-y-2.5">
            <p
              className={`flex items-center gap-2 text-[13px] ${expired ? "text-error-fg" : "text-ink-soft"}`}
            >
              <Clock3 aria-hidden size={15} className="flex-none" />
              {expired
                ? "报价已过期，请重新获取价格"
                : `报价有效期：${expiresInSeconds ?? "--"} 秒`}
            </p>
            {quote.disclosures.length > 0 && (
              <ul className="space-y-1.5 text-[12px] leading-5 text-ink-faint" aria-label="计费说明">
                {quote.disclosures.map((item) => (
                  <li key={`${item.key}-${item.copy_version}`} className="break-words">
                    {item.rendered_text}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {phase === "failed" && quote && (
          <p role="alert" className="flex items-start gap-2 text-[13px] text-error-fg">
            <TriangleAlert aria-hidden size={16} className="mt-0.5 flex-none" />
            {errorMessage ?? "操作未完成，请重新获取价格后再确认"}
          </p>
        )}

        <div className="flex flex-col-reverse gap-2.5 sm:flex-row sm:justify-end">
          <Button variant="soft" onClick={onCancel} disabled={submitting}>
            取消
          </Button>
          {expired ? (
            <Button onClick={onEstimate}>重新获取价格</Button>
          ) : (
            <>
              {phase === "failed" && (
                <Button variant="soft" onClick={onEstimate}>
                  重新获取价格
                </Button>
              )}
              <Button
                aria-label={confirmLabel}
                onClick={onConfirm}
                disabled={!confirmable || submitting}
              >
                {submitting ? (
                  <>
                    <Loader2 aria-hidden size={16} className="animate-spin" />
                    提交中…
                  </>
                ) : (
                    confirmLabel
                )}
              </Button>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
