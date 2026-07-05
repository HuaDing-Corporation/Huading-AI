"use client";

import { ChevronLeft, Download } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { friendlyVideoError } from "@/lib/api/video-error";
import { useBatch, useCancelBatch } from "@/lib/api/hooks";
import type { BatchStatus } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { BATCH_STATUS_LABEL } from "@/components/batch/batch-list";
import { copy } from "@/lib/copy";
import { useState } from "react";

const TASK_STATUS_LABEL: Record<string, string> = {
  queued: copy.status.queued,
  running: copy.batch.statusRunning,
  done: copy.status.done,
  failed: copy.batch.statusFailed,
  cancelled: copy.batch.statusCancelled
};

/**
 * 批次详情（BATCH-PROD-UI-0001）——轮询 GET /batches/{id}(≥5s，页面不活跃暂停)。每行：序号/状态/成片
 * 可播可下载/失败原因(脱敏)；顶部 cancel 批次(best-effort)。回列表。批内单条重试后端 v1 无端点，记 P2。
 */
export function BatchDetail({ batchId, onBack }: { batchId: string; onBack: () => void }) {
  const { data, isLoading, isError, refetch } = useBatch(batchId);
  const cancel = useCancelBatch();
  const [error, setError] = useState<string | null>(null);

  const batch = data?.batch;
  const tasks = data?.tasks ?? [];
  const active = batch?.status === "running";

  const onCancel = async () => {
    setError(null);
    try {
      await cancel.mutateAsync(batchId);
    } catch (err) {
      setError(errorText(err));
    }
  };
  return (
    <Card animateIn>
      <div className="mb-[14px] flex flex-wrap items-center justify-between gap-2">
        <button type="button" onClick={onBack} className="inline-flex items-center gap-1 rounded-field px-2 py-1 text-[13px] text-gold-deep transition-colors hover:bg-glass-soft">
          <ChevronLeft size={15} strokeWidth={2} /> {copy.batch.backToList}
        </button>
        {batch && (
          <span className="text-[12.5px] text-ink-soft">
            {BATCH_STATUS_LABEL[batch.status as BatchStatus]} · {copy.batch.progressLabel(batch.succeeded, batch.total)}
          </span>
        )}
      </div>
      <CardTitle className="mb-[12px]">{copy.batch.detailTitle}</CardTitle>

      {isLoading && !data ? (
        <p className="text-[13px] text-ink-soft">{copy.batch.listLoading}</p>
      ) : isError ? (
        <div className="text-[13px] text-error-fg">
          {copy.batch.listError}{" "}
          <button type="button" onClick={() => void refetch()} className="text-gold-deep underline">
            {copy.history.retry}
          </button>
        </div>
      ) : (
        <>
          {active && (
            <div className="mb-3">
              <Button variant="soft" size="sm" onClick={() => void onCancel()} disabled={cancel.isPending}>
                {cancel.isPending ? copy.batch.cancelling : copy.batch.cancelBatch}
              </Button>
              <p className="mt-1 text-[12px] text-ink-faint">{copy.batch.cancelHint}</p>
            </div>
          )}

          {error && (
            <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
              {error}
            </p>
          )}

          {/* 简报式 live region：仅播报进度/失败数摘要，避免每 5s 轮询整表(含视频/按钮)被 assertive 重播。 */}
          <p className="sr-only" role="status" aria-live="polite">
            {batch ? `${copy.batch.progressLabel(batch.succeeded, batch.total)} · ${batch.failed} ${copy.batch.statusFailed}` : ""}
          </p>

          <ul className="flex flex-col gap-2">
            {tasks.map((t) => (
              <li key={t.task_id} className="flex items-start gap-3 rounded-field border border-line-gold bg-glass-fill px-3 py-2.5">
                <span className="flex-none text-[12px] text-ink-faint">{copy.batch.rowIndex(t.row_index)}</span>
                <div className="min-w-0 flex-1">
                  <span className={`text-[12.5px] ${t.status === "failed" ? "text-error-fg" : "text-ink"}`}>
                    {TASK_STATUS_LABEL[t.status] ?? t.status}
                  </span>
                  {/* 批量子任务是视频任务(seedance_i2v/video_gen)：失败走 friendlyVideoError 友好中文映射，
                      绝不裸展示后端 error_message/error（真后端会回落 str(exc) 英文技术串）。VIDEO-ERR-MAP-UI。 */}
                  {t.status === "failed" && (
                    <p className="mt-0.5 text-[12px] text-error-fg">{friendlyVideoError(t.error_code)}</p>
                  )}
                  {t.status === "done" && t.video_url && (
                    <div className="mt-1.5 flex flex-wrap items-center gap-2">
                      <video controls preload="metadata" src={t.video_url} className="max-h-[180px] w-full rounded-field border border-line-gold bg-black/5" />
                      <a href={t.video_url} download className="inline-flex items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12px] text-gold-deep transition-colors hover:bg-glass-hover">
                        <Download size={13} strokeWidth={2} /> {copy.detail.download}
                      </a>
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}
