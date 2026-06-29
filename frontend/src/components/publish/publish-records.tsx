"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useDeletePublishRecord, usePublishRecords } from "@/lib/api/hooks";
import type { PublishPlatformId, PublishRecord, PublishStatus } from "@/lib/api/types";
import { Card, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { copy } from "@/lib/copy";

const STATUS_LABEL: Record<PublishStatus, string> = {
  draft: copy.publish.statusDraft,
  copied: copy.publish.statusCopied,
  published: copy.publish.statusPublished
};
const STATUS_CLASS: Record<PublishStatus, string> = {
  draft: "border-line-gold bg-glass-fill text-ink-soft",
  copied: "border-line-gold bg-glass-soft text-ink",
  published: "border-line-sel bg-chip-sel text-gold-deep"
};
const PLATFORM_NAME: Record<PublishPlatformId, string> = {
  douyin: copy.publish.platformDouyin,
  kuaishou: copy.publish.platformKuaishou,
  wxchannels: copy.publish.platformWechat,
  xiaohongshu: copy.publish.platformXiaohongshu,
  bilibili: copy.publish.platformBilibili
};

/**
 * 发布记录列表（PUBLISH-UI-0001）—— GET /publish/records，嵌套模型：每条记录(产物)含多平台
 * platforms[]{platform_id,status}。删除整条记录经 ConfirmDialog(危险确认 + 防连点)。
 */
export function PublishRecords() {
  const { data, isLoading, isError, refetch } = usePublishRecords();
  const del = useDeletePublishRecord();
  const [pendingDelete, setPendingDelete] = useState<PublishRecord | null>(null);
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
      <CardTitle className="mb-[14px]">{copy.publish.recordsTitle}</CardTitle>

      {isLoading ? (
        <p className="text-[13px] text-ink-soft">{copy.publish.recordsLoading}</p>
      ) : isError ? (
        <div className="text-[13px] text-error-fg">
          {copy.publish.recordsError}{" "}
          <button type="button" onClick={() => void refetch()} className="text-gold-deep underline">
            {copy.cover.retry}
          </button>
        </div>
      ) : items.length === 0 ? (
        <p className="text-[13px] text-ink-soft">{copy.publish.recordsEmpty}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((r) => (
            <li key={r.id} className="flex items-start gap-3 rounded-field border border-line-gold bg-glass-fill px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <span className="text-[12.5px] text-ink-soft">
                  {r.source_kind === "image" ? copy.publish.sourceImage : copy.publish.sourceVideo} · {r.source_task_id}
                </span>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {r.platforms.map((p) => (
                    <span
                      key={p.platform_id}
                      className={`inline-flex items-center gap-1 rounded-pill border px-2 py-0.5 text-[11px] ${STATUS_CLASS[p.status]}`}
                    >
                      {PLATFORM_NAME[p.platform_id] ?? p.platform_id} · {STATUS_LABEL[p.status]}
                    </span>
                  ))}
                </div>
              </div>
              <button
                type="button"
                onClick={() => {
                  setDeleteError(null);
                  setPendingDelete(r);
                }}
                aria-label={`${copy.publish.deleteRecord} ${r.source_task_id}`}
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
        title={copy.publish.deleteConfirmTitle}
        message={copy.publish.deleteConfirmMessage}
        confirmLabel={copy.publish.deleteConfirmBtn}
        danger
        submitting={del.isPending}
        error={deleteError}
        onConfirm={() => void onConfirmDelete()}
        onCancel={() => setPendingDelete(null)}
      />
    </Card>
  );
}
