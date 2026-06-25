"use client";

import { FileText } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useCopyDrafts } from "@/lib/api/hooks";
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

/** 历史「文案」tab：分页 GET /copy/drafts via useCopyDrafts。草稿非 video（不复用
 *  video HistoryList/TaskCard）→ 渲染只读草稿卡片（改写文案摘要 + 标题/话题 pill）。
 *  Loading/error/empty 状态与三 video tab 一致。Exported 供独立单测。 */
export function CopyDraftList() {
  const query = useCopyDrafts();
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];

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
  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-12 text-center">
        <FileText size={26} strokeWidth={1.6} className="text-ink-faint" />
        <p className="text-[13px] text-ink-soft">{copy.history.empty}</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2.5">
      {items.map((draft) => (
        <div key={draft.id} className="rounded-card border border-line-gold bg-glass-fill p-4">
          <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-ink">{draft.result_text}</p>
          {(draft.titles?.length || draft.topics?.length) ? (
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
    </div>
  );
}
