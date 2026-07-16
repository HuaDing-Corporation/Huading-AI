"use client";

import { useState } from "react";
import { ImageOff, ScanSearch, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ReversePromptResultView } from "@/components/workbench/reverse-prompt-result-view";
import {
  useDeleteReversePromptJob,
  useReversePromptJob,
  useReversePromptJobs,
  useSaveReversePrompt
} from "@/lib/api/hooks";
import { friendlyReverseError } from "@/lib/api/reverse-prompt";
import type { ReversePromptHistoryItem, ReverseSourceKind, WorkbenchPrefill } from "@/lib/api/reverse-prompt";
import { copy } from "@/lib/copy";

const formatCreatedAt = (iso: string) => (iso.includes("T") ? iso.replace("T", " ").slice(0, 16) : iso);

/**
 * 二级分类 chips —— 用户拍板：**在「提示词反推历史」tab 内部**再分图片/视频，不是在历史生成顶层加两个 tab。
 * 用 chip 而非二级 tablist，理由有三：
 *  ① BE 本就是**同一端点换 `?source_kind=` 筛选**（routes/reverse_prompt.py:64-87），是 filter 语义而非 panel 切换；
 *  ② 兄弟 tab「图片历史」已用 chips 做 6 分类子筛选（#176 定稿）→ 同一交互语言，一致性；
 *  ③ a11y：两层 `role="tablist"` 并置时读屏会连播两组 tab、用户分不清层级；chips 用 group+aria-pressed，
 *     读屏进入 tabpanel 后先遇到「反推来源」这个 group，从属关系天然说得清。
 */
const KIND_CHIPS: { key: ReverseSourceKind | "all"; label: string }[] = [
  { key: "all", label: copy.history.reverseKindAll },
  { key: "image", label: copy.history.reverseKindImage },
  { key: "video", label: copy.history.reverseKindVideo }
];

/** 状态徽标（BE DB CheckConstraint 5 值）；未知值由 copy.history.reverseStatus 原样透出，不吞。 */
function ReverseStatusBadge({ status }: { status: string }) {
  const tone =
    status === "failed"
      ? "bg-error-bg text-error-fg"
      : status === "succeeded" || status === "saved"
        ? "bg-chip-sel text-gold-deep"
        : "bg-glass-soft text-ink-soft";
  return <span className={`shrink-0 rounded-pill px-2 py-0.5 text-[11.5px] ${tone}`}>{copy.history.reverseStatus(status)}</span>;
}

/** 源缩略：BE 只对图片源生成缩略图，**视频源恒 null**（services/reverse_prompt.py:320-322）→ 显式占位，不留空洞。 */
function SourceThumb({ item, size = "sm" }: { item: ReversePromptHistoryItem; size?: "sm" | "lg" }) {
  const box = size === "lg" ? "h-24 w-24" : "h-14 w-14";
  if (item.source_thumbnail_url) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={item.source_thumbnail_url}
        alt={copy.history.reverseSourceAlt}
        className={`${box} shrink-0 rounded-field border border-line-gold object-cover`}
      />
    );
  }
  return (
    <div
      className={`${box} flex shrink-0 flex-col items-center justify-center gap-1 rounded-field border border-line-gold bg-glass-soft text-center`}
    >
      <ImageOff size={14} strokeWidth={1.8} className="text-ink-faint" />
      <span className="px-1 text-[10px] leading-tight text-ink-faint">{copy.history.reverseNoThumb}</span>
    </div>
  );
}

/**
 * 反推详情弹窗 —— 头部信息并集（源缩略 / 来源 / 状态 / 时间）+ **复用已生产验收的 ReversePromptResultView**
 * （完整反推结果 + 复制 + 保存 + 「带入生成」闭环）。严禁重造：带入链路 = fillTargetToPrefill → page 的
 * injectPrefill → 目标表单 useEffect 同步消费，与工作台反推页走同一条路。
 * 惰性：item=null 即不打开、useReversePromptJob 的 enabled 门控 → 关闭态不发请求（同 HistorySetDialog 惯例）。
 * 🔴 hideRegenerate：视频「重新反推」会二次扣 100 积分且历史场景无计费门 → 隐藏，避免误扣（与 reverse-video 一致）。
 */
function ReverseDetailDialog({
  item,
  onClose,
  onApplyPrefill
}: {
  item: ReversePromptHistoryItem | null;
  onClose: () => void;
  onApplyPrefill?: (prefill: WorkbenchPrefill) => void;
}) {
  const query = useReversePromptJob(item?.id);
  const save = useSaveReversePrompt();
  const job = query.data;
  return (
    <Dialog
      open={item !== null}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="w-[min(94vw,880px)] max-h-[85vh] overflow-y-auto">
        <div className="mb-3 flex items-start gap-3">
          {item ? <SourceThumb item={item} size="lg" /> : null}
          <div className="min-w-0 flex-1">
            <DialogTitle className="text-base font-semibold text-ink">{copy.history.reverseDetailTitle}</DialogTitle>
            <DialogDescription className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-soft">
              {item ? (
                <>
                  <span>{copy.history.reverseKindTag(item.source_kind)}</span>
                  <ReverseStatusBadge status={item.status} />
                  <span className="tabular-nums">{formatCreatedAt(item.created_at)}</span>
                </>
              ) : null}
            </DialogDescription>
          </div>
        </div>

        {query.isLoading ? (
          <p className="py-8 text-center text-[13px] text-ink-soft">{copy.history.loading}</p>
        ) : query.isError ? (
          <div className="flex flex-col items-center gap-2 py-8 text-center">
            <p role="alert" className="text-[13px] text-error-fg">
              {copy.history.error}
            </p>
            <Button variant="soft" size="sm" onClick={() => void query.refetch()}>
              {copy.history.retry}
            </Button>
          </div>
        ) : job?.status === "failed" ? (
          // failed：BE 无 result、有 error_*（走既有 friendlyReverseError，绝不回落裸技术串）。
          <p role="alert" className="rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
            {friendlyReverseError(job.error_code, job.error_message)}
          </p>
        ) : job?.result ? (
          <ReversePromptResultView
            result={job.result}
            onApply={(prefill) => {
              onApplyPrefill?.(prefill);
              onClose();
              // 历史区在工作台**页面底部**，带入虽已切好 mode，但视口仍停在这里 → 用户看不到被预填的表单。
              // 故滚回顶部（工作台反推页的带入就在表单原地，不需要滚，所以这段只放历史侧、不污染 page 的
              // injectPrefill）。尊重 prefers-reduced-motion：reduce 时直接跳，不做平滑动画。
              if (typeof window !== "undefined") {
                const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
                window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
              }
            }}
            onRegenerate={() => undefined}
            hideRegenerate
            onSave={() => void save.mutateAsync(job.id).catch(() => undefined)}
            saving={save.isPending}
            saved={!!job.saved_at}
          />
        ) : (
          // queued / running：BE 尚无 result（视频反推是异步的）。
          <p className="py-8 text-center text-[13px] text-ink-soft">{copy.history.reversePendingHint}</p>
        )}
      </DialogContent>
    </Dialog>
  );
}

/**
 * 提示词反推历史（HISTORY-VIDEO-REVERSE-UI-0001）——「历史生成」第 6 个 tab 的内容。
 * 列表 GET /reverse-prompt/jobs（page 制分页，source_kind 省略=全部）；每条：看详情 / 带入 / 删除（软删）。
 * 🔴 BE 列表项**不含 result**（schemas:97-103 只有 6 字段）→ 「带入」必须先取详情拿 fill_targets，故带入按钮
 *    只在详情弹窗内，不挂列表卡（挂卡上就得为每条预拉详情，纯浪费）。
 * Exported 供单测直接渲染（Radix tab 激活在 jsdom 不可靠，与 HistoryList/PhotoHistory 同惯例）。
 */
export function ReverseHistoryList({ onApplyPrefill }: { onApplyPrefill?: (prefill: WorkbenchPrefill) => void } = {}) {
  const [kind, setKind] = useState<ReverseSourceKind | "all">("all");
  const query = useReversePromptJobs(kind === "all" ? undefined : kind);
  const del = useDeleteReversePromptJob();
  const [detail, setDetail] = useState<ReversePromptHistoryItem | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const items = query.data?.pages.flatMap((page) => page.items) ?? [];

  const onConfirmDelete = async () => {
    if (!confirmDelete) return;
    setActionError(null);
    try {
      await del.mutateAsync(confirmDelete);
      setConfirmDelete(null);
    } catch {
      setActionError(copy.history.deleteFailed);
    }
  };

  return (
    <div>
      {/* 二级分类（tab 内再分）：chip + group/aria-pressed，与图片历史 tab 同一交互语言。 */}
      <div role="group" aria-label={copy.history.reverseKindLabel} className="mb-3 flex flex-wrap gap-1.5">
        {KIND_CHIPS.map((c) => (
          <Chip
            key={c.key}
            selected={kind === c.key}
            onClick={() => setKind(c.key)}
            className="px-3 py-1.5 text-[12.5px]"
          >
            {c.label}
          </Chip>
        ))}
      </div>

      {/* 删除错误只在确认弹窗内呈现（见下方 ConfirmDialog 的 error）——本列表的 actionError 只由删除产生，
          再在列表顶部复述一遍会造成同文案双份（读屏念两遍、测试也得靠 scope 绕）。 */}
      {query.isLoading ? (
        <p className="py-10 text-center text-[13px] text-ink-soft">{copy.history.loading}</p>
      ) : query.isError ? (
        <div className="flex flex-col items-center gap-2 py-10 text-center">
          <p className="text-[13px] text-error-fg">{copy.history.error}</p>
          <Button variant="soft" size="sm" onClick={() => void query.refetch()}>
            {copy.history.retry}
          </Button>
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-12 text-center">
          <ScanSearch size={26} strokeWidth={1.6} className="text-ink-faint" />
          <p className="text-[13px] text-ink-soft">{copy.history.reverseEmpty}</p>
        </div>
      ) : (
        <>
          <ul className="flex flex-col gap-2">
            {items.map((item) => (
              <li
                key={item.id}
                className="flex items-center gap-3 rounded-field border border-line-gold bg-glass-fill p-2.5"
              >
                <SourceThumb item={item} />
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <span className="text-[12px] text-ink-faint">{copy.history.reverseKindTag(item.source_kind)}</span>
                    <ReverseStatusBadge status={item.status} />
                    <span className="text-[11.5px] tabular-nums text-ink-faint">{formatCreatedAt(item.created_at)}</span>
                  </div>
                  <p className="line-clamp-2 text-[12.5px] leading-relaxed text-ink-soft">
                    {item.summary ?? copy.history.reverseNoSummary}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <Button variant="soft" size="sm" onClick={() => setDetail(item)}>
                    {copy.tasks.open}
                  </Button>
                  <button
                    type="button"
                    aria-label={copy.history.deleteItem}
                    disabled={del.isPending && del.variables === item.id}
                    onClick={() => setConfirmDelete(item.id)}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-field text-ink-faint outline-none transition-colors hover:bg-error-bg hover:text-error-fg focus-visible:shadow-focus-gold disabled:opacity-50"
                  >
                    <Trash2 size={14} strokeWidth={1.8} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
          {query.hasNextPage ? (
            <div className="mt-3 flex justify-center">
              <Button variant="soft" size="sm" onClick={() => void query.fetchNextPage()} disabled={query.isFetchingNextPage}>
                {copy.history.loadMore}
              </Button>
            </div>
          ) : null}
        </>
      )}

      <ReverseDetailDialog item={detail} onClose={() => setDetail(null)} onApplyPrefill={onApplyPrefill} />

      {/* 删除确认。FIX2：文案讲**用户可观察的后果**，不讲 BE 实现 —— BE 的软删是运维保险（不碰媒体、出事能救），
          但用户侧列表过滤已删 + 详情 404 + **没有恢复入口** → 「可恢复」是骗人；而「永久删除」又谎报了实现
          （数据其实都在）。故用 reverseDeleteConfirmMsg「将从历史移除，无法撤销。」 */}
      <ConfirmDialog
        open={!!confirmDelete}
        title={copy.history.reverseDeleteConfirmTitle}
        message={copy.history.reverseDeleteConfirmMsg}
        confirmLabel={copy.history.deleteConfirmBtn}
        submitting={del.isPending}
        error={actionError}
        onConfirm={() => void onConfirmDelete()}
        onCancel={() => {
          setConfirmDelete(null);
          setActionError(null);
        }}
      />
    </div>
  );
}
