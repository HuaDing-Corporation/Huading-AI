"use client";

import { useState } from "react";
import { Loader2, Trash2 } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useBrandVoices, useDeleteBrandVoice } from "@/lib/api/hooks";
import type { BrandVoice, BrandVoiceStatus } from "@/lib/api/types";
import { Card, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { copy } from "@/lib/copy";

const STATUS_LABEL: Record<BrandVoiceStatus, string> = {
  processing: copy.brandVoice.statusProcessing,
  ready: copy.brandVoice.statusReady,
  failed: copy.brandVoice.statusFailed
};
// 状态徽章配色：token 化（处理中=柔金、可用=金深、失败=错误色），不硬编码 hex。
const STATUS_CLASS: Record<BrandVoiceStatus, string> = {
  processing: "border-line-gold bg-glass-fill text-ink-soft",
  ready: "border-line-sel bg-chip-sel text-gold-deep",
  failed: "border-line-gold bg-error-bg text-error-fg"
};

function StatusBadge({ status }: { status: BrandVoiceStatus }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-pill border px-2.5 py-0.5 text-[11.5px] ${STATUS_CLASS[status]}`}>
      {status === "processing" && <Loader2 size={11} strokeWidth={2.2} className="animate-spin" />}
      {STATUS_LABEL[status]}
    </span>
  );
}

/**
 * 品牌音色列表（BRAND-VOICE-UI-0001）—— GET /brand-voices，status 徽章(处理中/可用/失败)，
 * 处理中由 useBrandVoices 轮询；ready 可试听；删除经 ConfirmDialog(危险确认 + 防连点)。
 * 复用 ConfirmDialog/Card，唯一 hooks 调用方为列表自身。
 */
export function BrandVoiceList() {
  const { data, isLoading, isError, refetch } = useBrandVoices();
  const del = useDeleteBrandVoice();
  const [pendingDelete, setPendingDelete] = useState<BrandVoice | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const items = data ?? [];

  const onConfirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleteError(null);
    try {
      await del.mutateAsync(pendingDelete.id);
      setPendingDelete(null);
    } catch (err) {
      setDeleteError(errorText(err));
    }
  };

  return (
    <Card animateIn>
      <CardTitle className="mb-[14px]">{copy.brandVoice.listTitle}</CardTitle>

      {isLoading ? (
        <p className="text-[13px] text-ink-soft">{copy.brandVoice.listLoading}</p>
      ) : isError ? (
        <div className="text-[13px] text-error-fg">
          {copy.brandVoice.listError}{" "}
          <button type="button" onClick={() => void refetch()} className="text-gold-deep underline">
            {copy.cover.retry}
          </button>
        </div>
      ) : items.length === 0 ? (
        <p className="text-[13px] text-ink-soft">{copy.brandVoice.listEmpty}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((v) => (
            <li
              key={v.id}
              className="flex items-center gap-3 rounded-field border border-line-gold bg-glass-fill px-3 py-2.5"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[13px] text-ink">{v.name}</span>
                  <StatusBadge status={v.status} />
                </div>
                {v.status === "processing" && (
                  <p className="mt-0.5 text-[11.5px] text-ink-faint">{copy.brandVoice.processingHint}</p>
                )}
                {/* §8：失败态仅徽章「失败」，后端不返 error_message，不显额外原因。 */}
              </div>
              {/* §8 v1：列表不试听克隆音色(后端不返 sample)；创建前的本地录音试听保留在创建区。 */}
              <button
                type="button"
                onClick={() => {
                  setDeleteError(null);
                  setPendingDelete(v);
                }}
                aria-label={`${copy.brandVoice.delete} ${v.name}`}
                className="flex h-8 w-8 flex-none items-center justify-center rounded-mark text-ink-soft hover:bg-error-bg hover:text-error-fg"
              >
                <Trash2 size={15} strokeWidth={2} />
              </button>
            </li>
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={!!pendingDelete}
        title={copy.brandVoice.deleteConfirmTitle}
        message={copy.brandVoice.deleteConfirmMessage}
        confirmLabel={copy.brandVoice.deleteConfirmBtn}
        danger
        submitting={del.isPending}
        error={deleteError}
        onConfirm={() => void onConfirmDelete()}
        onCancel={() => setPendingDelete(null)}
      />
    </Card>
  );
}
