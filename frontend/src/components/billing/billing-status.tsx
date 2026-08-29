"use client";

import { CircleCheck, CircleDashed, CircleDollarSign, LockKeyhole, RotateCw, Undo2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { parseBillingSummary } from "@/lib/api/billing";
import { cn } from "@/lib/utils";

export interface BillingStatusProps {
  summary: unknown;
  querying?: boolean;
  onContinueLookup?: () => void;
  onDismiss?: () => void;
  className?: string;
}

export function BillingStatus({
  summary,
  querying = false,
  onContinueLookup,
  onDismiss,
  className
}: BillingStatusProps) {
  const parsed = parseBillingSummary(summary);
  let content;
  let style = "border-line-gold bg-white/45 text-ink-soft";

  if (!parsed) {
    content = (
      <>
        <CircleDashed aria-hidden size={16} className={querying ? "animate-spin" : undefined} />
        <span>计费结果确认中</span>
      </>
    );
  } else if (parsed.status === "reserved") {
    style = "border-line-gold bg-queue-bg text-queue-fg";
    content = (
      <>
        <LockKeyhole aria-hidden size={16} />
        <span>已冻结 {parsed.held_credits} 积分</span>
      </>
    );
  } else if (parsed.status === "settled" && parsed.requested_credits === 0) {
    style = "border-line-gold bg-success-bg text-success-fg";
    content = (
      <>
        <CircleCheck aria-hidden size={16} />
        <span>免费服务，已完成（未扣积分）</span>
      </>
    );
  } else if (parsed.status === "settled") {
    style = "border-line-gold bg-success-bg text-success-fg";
    content = (
      <>
        <CircleDollarSign aria-hidden size={16} />
        <span>已结算 {parsed.settled_credits} 积分</span>
      </>
    );
  } else if (parsed.status === "partially_settled") {
    style = "border-line-gold bg-run-bg text-run-fg";
    content = (
      <>
        <RotateCw aria-hidden size={16} />
        <span>
          部分结算 {parsed.settled_credits} 积分，已释放 {parsed.released_credits} 积分
        </span>
      </>
    );
  } else {
    style = "border-line-gold bg-error-bg text-error-fg";
    content = (
      <>
        <Undo2 aria-hidden size={16} />
        <span>未扣款，已释放 {parsed.released_credits} 积分</span>
      </>
    );
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex min-w-0 flex-wrap items-center gap-2 rounded-field border px-3 py-2.5 text-[13px] font-medium",
        style,
        className
      )}
    >
      {content}
      {((querying && onContinueLookup) || onDismiss) && (
        <div className="ml-auto flex flex-none items-center gap-2">
          {querying && onContinueLookup && (
            <Button type="button" variant="soft" size="sm" onClick={onContinueLookup}>
              继续查询
            </Button>
          )}
          {onDismiss && (
            <Button
              type="button"
              variant="soft"
              size="sm"
              aria-label="关闭计费结果"
              onClick={onDismiss}
            >
              <X aria-hidden size={14} />
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
