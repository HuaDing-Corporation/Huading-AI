"use client";

import { useState } from "react";
import { Clapperboard, FileText, Images, Store, Trash2, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger, tabTriggerClass } from "@/components/ui/tabs";
import { CopyDraftList } from "@/components/tasks/copy-draft-list";
import { TaskCard } from "@/components/tasks/task-card";
import { useClearVideos, useDeleteVideo, useVideoHistory } from "@/lib/api/hooks";
import { fromVideoRead } from "@/lib/sse/progress-mapping";
import { copy } from "@/lib/copy";

/** One mode's history: paginated GET /videos?mode=(&kind=) via useVideoHistory; reuses
 *  TaskCard. 每条带删除(trash→确认→DELETE /videos/{id})、tab 顶「清空」(确认→DELETE
 *  /videos?mode=)。视频/图片=硬删不可恢复(danger 确认)。防连点(pending 禁用)。
 *  Exported for direct unit testing per mode (Radix tab activation unreliable in jsdom). */
export function HistoryList({ mode, kind }: { mode: string; kind?: string }) {
  const router = useRouter();
  const query = useVideoHistory(mode, kind);
  const deleteVideo = useDeleteVideo();
  const clearVideos = useClearVideos();
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const items = query.data?.pages.flatMap((page) => page.items) ?? [];

  const onConfirmDelete = async () => {
    if (!confirmDelete) return;
    setActionError(null);
    try {
      await deleteVideo.mutateAsync(confirmDelete);
      setConfirmDelete(null);
    } catch {
      setActionError(copy.history.deleteFailed);
    }
  };
  const onConfirmClear = async () => {
    setActionError(null);
    try {
      await clearVideos.mutateAsync(mode);
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
    <div>
      {actionError && (
        <p role="alert" className="mb-2 rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
          {actionError}
        </p>
      )}

      {items.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-12 text-center">
          <Clapperboard size={26} strokeWidth={1.6} className="text-ink-faint" />
          <p className="text-[13px] text-ink-soft">{copy.history.empty}</p>
        </div>
      ) : (
        <>
          <div className="mb-1 flex justify-end">
            <button
              type="button"
              onClick={() => setConfirmClear(true)}
              className="inline-flex items-center gap-1 rounded-field px-2 py-1 text-[12px] text-ink-faint transition-colors hover:bg-error-bg hover:text-error-fg focus-visible:shadow-focus-gold"
            >
              <Trash2 size={13} strokeWidth={1.8} /> {copy.history.clearAll}
            </button>
          </div>
          {items.map((item) => (
            <TaskCard
              key={item.id}
              // The tab's mode is authoritative for this list → photo items render <img>.
              task={{ ...fromVideoRead(item), mode }}
              onOpen={(id) => router.push(`/videos/${id}`)}
              onRetry={() => undefined}
              onUrlError={() => void query.refetch()}
              onDelete={(id) => setConfirmDelete(id)}
              deleting={deleteVideo.isPending && deleteVideo.variables === item.id}
            />
          ))}
          {query.hasNextPage ? (
            <div className="mt-3 flex justify-center">
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

      {/* 删除单条确认(视频/图片=硬删不可恢复) */}
      <ConfirmDialog
        open={!!confirmDelete}
        title={copy.history.deleteConfirmTitle}
        message={copy.history.deleteConfirmHard}
        confirmLabel={copy.history.deleteConfirmBtn}
        danger
        submitting={deleteVideo.isPending}
        error={actionError}
        onConfirm={() => void onConfirmDelete()}
        onCancel={() => {
          setConfirmDelete(null);
          setActionError(null);
        }}
      />
      {/* 清空该模块确认(硬删不可恢复) */}
      <ConfirmDialog
        open={confirmClear}
        title={copy.history.clearConfirmTitle}
        message={mode === "photo" ? copy.history.clearConfirmPhotoHard : copy.history.clearConfirmHard}
        confirmLabel={copy.history.clearConfirmBtn}
        danger
        submitting={clearVideos.isPending}
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

/** 图片历史 + 「全部图片 / 仅封面」toggle → GET /videos?mode=photo(&kind=cover)。
 *  仅封面接真后端(封面建成 photo VideoTask kind=cover)。Exported 供单测。 */
export function PhotoHistory() {
  const [coverOnly, setCoverOnly] = useState(false);
  return (
    <div>
      <div className="mb-3 flex gap-1.5">
        <Chip
          selected={!coverOnly}
          onClick={() => setCoverOnly(false)}
          className="px-3 py-1.5 text-[12.5px]"
        >
          {copy.history.filterAllImages}
        </Chip>
        <Chip
          selected={coverOnly}
          onClick={() => setCoverOnly(true)}
          className="px-3 py-1.5 text-[12.5px]"
        >
          {copy.history.filterCovers}
        </Chip>
      </div>
      {/* key 随筛选变更 → 切「全部/仅封面」时 HistoryList 全新实例，重置陈旧 actionError(RV #5) */}
      <HistoryList key={coverOnly ? "cover" : "all"} mode="photo" kind={coverOnly ? "cover" : undefined} />
    </div>
  );
}

/** 历史生成 — 4 tabs: 数字人 / 电商 / 照片(+仅封面筛) + 文案. */
export function GenerationHistory() {
  return (
    <Card animateIn>
      <CardTitle className="mb-3.5">{copy.history.title}</CardTitle>
      <Tabs defaultValue="avatar_talk">
        <TabsList
          aria-label={copy.history.title}
          className="mb-3 flex flex-wrap gap-1.5 rounded-pill border border-line-gold bg-glass-soft p-1"
        >
          <TabsTrigger value="avatar_talk" className={tabTriggerClass}>
            <UserRound size={14} strokeWidth={1.8} /> {copy.history.tabAvatar}
          </TabsTrigger>
          <TabsTrigger value="seedance_i2v" className={tabTriggerClass}>
            <Store size={14} strokeWidth={1.8} /> {copy.history.tabEcom}
          </TabsTrigger>
          <TabsTrigger value="photo" className={tabTriggerClass}>
            <Images size={14} strokeWidth={1.8} /> {copy.history.tabPhoto}
          </TabsTrigger>
          <TabsTrigger value="copywriting" className={tabTriggerClass}>
            <FileText size={14} strokeWidth={1.8} /> {copy.history.tabCopy}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="avatar_talk" className="outline-none">
          <HistoryList mode="avatar_talk" />
        </TabsContent>
        <TabsContent value="seedance_i2v" className="outline-none">
          <HistoryList mode="seedance_i2v" />
        </TabsContent>
        <TabsContent value="photo" className="outline-none">
          <PhotoHistory />
        </TabsContent>
        <TabsContent value="copywriting" className="outline-none">
          <CopyDraftList />
        </TabsContent>
      </Tabs>
    </Card>
  );
}
