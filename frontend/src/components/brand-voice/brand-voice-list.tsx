"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";

import { Card, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { errorText } from "@/lib/api/error-text";
import { useBrandVoices, useDeleteBrandVoice } from "@/lib/api/hooks";
import type { BrandVoice, BrandVoiceDeliveryStatus } from "@/lib/api/types";
import { copy } from "@/lib/copy";

const STATUS_LABEL: Record<BrandVoiceDeliveryStatus, string> = {
  awaiting_fulfillment: "等待人工交付",
  active: "可用",
  expired: "已过期",
  rejected: "已拒绝"
};

export function BrandVoiceList({ onRenew }: { onRenew?: (voice: BrandVoice) => void }) {
  const voices = useBrandVoices();
  const del = useDeleteBrandVoice();
  const [pendingDelete, setPendingDelete] = useState<BrandVoice | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const items = voices.data ?? [];

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleteError(null);
    try {
      await del.mutateAsync(pendingDelete.id);
      setPendingDelete(null);
    } catch (caught) {
      setDeleteError(errorText(caught));
    }
  };

  return (
    <Card animateIn>
      <CardTitle className="mb-[14px]">{copy.brandVoice.listTitle}</CardTitle>
      {voices.isLoading ? <p className="text-[13px] text-ink-soft">{copy.brandVoice.listLoading}</p> : voices.isError ? (
        <p role="alert" className="text-[13px] text-error-fg">{copy.brandVoice.listError}</p>
      ) : items.length === 0 ? <p className="text-[13px] text-ink-soft">{copy.brandVoice.listEmpty}</p> : (
        <ul className="space-y-2">
          {items.map((voice) => (
            <li key={voice.id} className="flex min-w-0 flex-wrap items-center gap-3 rounded-field border border-line-gold bg-glass-fill px-3 py-2.5">
              <div className="min-w-[10rem] flex-1">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="break-words text-[13px] text-ink">{voice.name}</span>
                  <span className="rounded-pill border border-line-gold bg-glass-soft px-2 py-0.5 text-[11.5px] text-ink-soft">{STATUS_LABEL[voice.delivery_status]}</span>
                </div>
                <p className="mt-1 text-[11.5px] text-ink-faint">{voice.provider.includes("doubao") ? "豆包人工交付" : "CosyVoice"}</p>
                {voice.expires_at && <p className="mt-1 text-[11.5px] text-ink-soft">到期时间：{new Date(voice.expires_at).toLocaleString()}</p>}
                {voice.delivery_status === "awaiting_fulfillment" && <p className="mt-1 text-[11.5px] text-queue-fg">等待平台人工交付，不会显示为供应商生成中</p>}
              </div>
              {voice.delivery_status === "expired" && onRenew && (
                <button type="button" onClick={() => onRenew(voice)} className="rounded-field border border-line-gold px-3 py-1.5 text-[12px] text-gold-deep hover:bg-glass-hover">使用新音频续期</button>
              )}
              <button type="button" aria-label={`${copy.brandVoice.delete} ${voice.name}`} onClick={() => setPendingDelete(voice)} className="flex h-8 w-8 items-center justify-center rounded-mark text-ink-soft hover:bg-error-bg hover:text-error-fg"><Trash2 size={15} /></button>
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={!!pendingDelete}
        title={copy.brandVoice.deleteConfirmTitle}
        message="删除只会移除该音色，不代表退款；任何退款均以人工订单返回状态为准。"
        confirmLabel={copy.brandVoice.deleteConfirmBtn}
        danger
        submitting={del.isPending}
        error={deleteError}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setPendingDelete(null)}
      />
    </Card>
  );
}
