"use client";

import { useState } from "react";
import { FileText, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useClearCopyDrafts, useCopyDrafts, useDeleteCopyDraft } from "@/lib/api/hooks";
import { copy } from "@/lib/copy";

const pillClass =
  "rounded-pill border border-line-gold bg-glass-soft px-2.5 py-1 text-[12px] text-ink-soft";

/** 只读 pill 列表 — 标题与话题两段同构，抽此本地 helper 消除复制粘贴。 */
function pills(keyPrefix: string, items: string[] | null | undefined) {
  return (items ?? []).map((item, i) => (
    <span key={`${keyPrefix}-${i}`} className={pillClass}>
      {item}
    </span>
  ));
}

/** 历史「文案」tab：分页 GET /copy/drafts via useCopyDrafts。草稿非 video(独立列表)。
 *  每条带删除(trash→确认→DELETE /copy/drafts/{id})、顶「清空」(确认→DELETE /copy/drafts)。
 *  文案=软删可恢复(确认非危险样式)。防连点(pending 禁用)。Exported 供独立单测。 */
export function CopyDraftList() {
  const query = useCopyDrafts();
  const deleteDraft = useDeleteCopyDraft();
  const clearDrafts = useClearCopyDrafts();
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const items = query.data?.pages.flatMap((page) => page.items) ?? [];

  const onConfirmDelete = async () => {
    if (!confirmDelete) return;
    setActionError(null);
    try {
      await deleteDraft.mutateAsync(confirmDelete);
      setConfirmDelete(null);
    } catch {
      setActionError(copy.history.deleteFailed);
    }
  };
  const onConfirmClear = async () => {
    setActionError(null);
    try {
      await clearDrafts.mutateAsync();
      setConfirmClear(false);
    } catch {
      setActionError(copy.history.clearFailed);
    }
  };

  if (query.isLoading) {
    return <p className="py-10 text-center text-[13px] text-ink-soft">{copy.history.loading}</p>;
  }
  if (query.isError) {
    return (
      <div className="flex flex-col items-center gap-2 py-10 text-center">
        <p className="text-[13px] text-error-fg">{copy.history.error}</p>
        <Button variant="soft" size="sm" onClick={() => void query.refetch()}>
          {copy.history.retry}
        </Button>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2.5">
      {actionError && (
        <p role="alert" className="rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
          {actionError}
        </p>
      )}

      {items.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-12 text-center">
          <FileText size={26} strokeWidth={1.6} className="text-ink-faint" />
          <p className="text-[13px] text-ink-soft">{copy.history.empty}</p>
        </div>
      ) : (
        <>
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setConfirmClear(true)}
              className="inline-flex items-center gap-1 rounded-field px-2 py-1 text-[12px] text-ink-faint transition-colors hover:bg-error-bg hover:text-error-fg focus-visible:shadow-focus-gold"
            >
              <Trash2 size={13} strokeWidth={1.8} /> {copy.history.clearAll}
            </button>
          </div>
          {items.map((draft) => (
            <div key={draft.id} className="rounded-card border border-line-gold bg-glass-fill p-4">
              <div className="flex items-start gap-2">
                <p className="min-w-0 flex-1 whitespace-pre-wrap text-[13px] leading-relaxed text-ink">
                  {draft.result_text}
                </p>
                <button
                  type="button"
                  onClick={() => setConfirmDelete(draft.id)}
                  disabled={deleteDraft.isPending && deleteDraft.variables === draft.id}
                  aria-label={copy.history.deleteItem}
                  title={copy.history.deleteItem}
                  className="flex h-8 w-8 flex-none items-center justify-center rounded-field text-ink-faint transition-colors hover:bg-error-bg hover:text-error-fg focus-visible:shadow-focus-gold disabled:pointer-events-none disabled:opacity-50"
                >
                  <Trash2 size={15} strokeWidth={1.8} />
                </button>
              </div>
              {draft.titles?.length || draft.topics?.length ? (
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  {pills("ti", draft.titles)}
                  {pills("to", draft.topics)}
                </div>
              ) : null}
            </div>
          ))}
          {query.hasNextPage ? (
            <div className="mt-1 flex justify-center">
              <Button
                variant="soft"
                size="sm"
                onClick={() => void query.fetchNextPage()}
                disabled={query.isFetchingNextPage}
              >
                {copy.history.loadMore}
              </Button>
            </div>
          ) : null}
        </>
      )}

      {/* 删除单条确认(文案=软删可恢复，非危险样式) */}
      <ConfirmDialog
        open={!!confirmDelete}
        title={copy.history.deleteConfirmTitle}
        message={copy.history.deleteConfirmSoft}
        confirmLabel={copy.history.deleteConfirmBtn}
        submitting={deleteDraft.isPending}
        error={actionError}
        onConfirm={() => void onConfirmDelete()}
        onCancel={() => {
          setConfirmDelete(null);
          setActionError(null);
        }}
      />
      {/* 清空确认(文案=软删可恢复) */}
      <ConfirmDialog
        open={confirmClear}
        title={copy.history.clearConfirmTitle}
        message={copy.history.clearConfirmSoft}
        confirmLabel={copy.history.clearConfirmBtn}
        submitting={clearDrafts.isPending}
        error={actionError}
        onConfirm={() => void onConfirmClear()}
        onCancel={() => {
          setConfirmClear(false);
          setActionError(null);
        }}
      />
    </div>
  );
}
