"use client";

import { useState } from "react";
import { Clapperboard, FileText, Images, ScanSearch, Store, Trash2, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger, tabTriggerClass } from "@/components/ui/tabs";
import { CopyDraftList } from "@/components/tasks/copy-draft-list";
import { ReverseHistoryList } from "@/components/tasks/reverse-history-list";
import { TaskCard } from "@/components/tasks/task-card";
import { HistoryGrid } from "@/components/history/history-grid";
import { VideoDetailDialog, type VideoDetailPayload } from "@/components/history/video-detail-dialog";
import { VideoLightbox } from "@/components/history/video-lightbox";
import { useClearVideos, useDeleteVideo, useVideoHistory } from "@/lib/api/hooks";
import { fromVideoRead, type TrackedTask } from "@/lib/sse/progress-mapping";
import { copy } from "@/lib/copy";
import type { HistoryCategory } from "@/lib/api/history-images";
import type { WorkbenchPrefill } from "@/lib/api/reverse-prompt";

/** One mode's history: paginated GET /videos?mode=(&kind=) via useVideoHistory; reuses
 *  TaskCard. 每条带删除(trash→确认→DELETE /videos/{id})、tab 顶「清空」(确认→DELETE
 *  /videos?mode=)。视频/图片=硬删不可恢复(danger 确认)。防连点(pending 禁用)。
 *  Exported for direct unit testing per mode (Radix tab activation unreliable in jsdom). */
/** 三视频 tab 的 mode → 中文（视频详情弹窗的「分类」项；对齐图片详情弹窗的信息并集）。 */
const MODE_LABEL: Record<string, string> = {
  avatar_talk: copy.history.tabAvatar,
  seedance_i2v: copy.history.tabEcom,
  video_gen: copy.history.tabVideoGen
};

export function HistoryList({ mode, kind }: { mode: string; kind?: string }) {
  const router = useRouter();
  const query = useVideoHistory(mode, kind);
  const deleteVideo = useDeleteVideo();
  const clearVideos = useClearVideos();
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  // HISTORY-VIDEO-DIALOG-UI-0001：点内容 → 大屏播放；「查看详情」→ 详情弹窗（与图片 tab 同款交互语言）。
  // 🔴 FIX1：**只存 id，不存任务快照**。存快照 = 弹窗里的 playbackUrl 冻结在点击那一刻 →
  // presign 过期后即使 refetch 拿回了新 URL，弹窗还在用旧的 → 播放器永远救不回来（这正是 P1-1）。
  // 存 id 从**最新** items 派生，refetch 一到，弹窗里的 <video src> 自然跟着换。
  const [detailId, setDetailId] = useState<string | null>(null);
  const [lightboxId, setLightboxId] = useState<string | null>(null);

  const items = query.data?.pages.flatMap((page) => page.items) ?? [];

  // The tab's mode is authoritative for this list → photo items render <img>.
  const taskOf = (item: (typeof items)[number]): TrackedTask => ({ ...fromVideoRead(item), mode });
  // 派生不到（该条已被删除/清空）→ null → 弹窗自动关闭，不会挂着一个指向已消失记录的界面。
  // refetch 进行中不会走到这里的空态：react-query 保留上一份 data，items 不会瞬间清空。
  const lightboxItem = lightboxId === null ? undefined : items.find((i) => i.id === lightboxId);
  const lightboxTask = lightboxItem ? taskOf(lightboxItem) : null;
  const detailItem = detailId === null ? undefined : items.find((i) => i.id === detailId);
  const detailPayload: VideoDetailPayload | null = detailItem
    ? { task: taskOf(detailItem), createdAt: detailItem.created_at, modeLabel: MODE_LABEL[mode] ?? mode }
    : null;

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
              task={taskOf(item)}
              // 「查看详情」→ 详情弹窗（升级前是直接 router.push）。TaskCard 的 onOpen 契约未变，只是这里改了
              // 接法；跳详情页的能力**没丢** —— 移到弹窗内的「打开详情页」（见 VideoDetailDialog）。
              // created_at 从**列表项**带入：TrackedTask 无此字段，而 progress-mapping 是 SSE 与列表共用的映射。
              onOpen={() => setDetailId(item.id)}
              onOpenMedia={() => setLightboxId(item.id)}
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

      {/* 大屏播放：点「播放视频」→ overlay 内联播放；关闭即卸载 <video>（Radix Portal 在 open=false 时不渲染）。
          onUrlError → refetch：与卡片内联播放器同一个动作，弹窗里的 src 由上面的派生自动跟进。 */}
      <VideoLightbox
        src={lightboxTask?.playbackUrl ?? null}
        poster={lightboxTask?.thumbnailUrl}
        title={lightboxTask?.topic ?? ""}
        // 按**派生结果**开合、而非 id 是否存在：那条记录若被「清空」冲掉，id 还在但派生为 null，
        // 按 id 开就会留下一个空白 overlay。与 VideoDetailDialog（open={detail !== null}）同口径。
        open={lightboxTask !== null}
        onClose={() => setLightboxId(null)}
        onUrlError={() => void query.refetch()}
      />
      {/* 详情弹窗：信息并集（生成时间/状态/模式/时长/AI 标识 + 播放 + 下载）+ 「打开详情页」（跳转能力零回归）。 */}
      <VideoDetailDialog
        detail={detailPayload}
        onClose={() => setDetailId(null)}
        onOpenPage={(id) => router.push(`/videos/${id}`)}
        onUrlError={() => void query.refetch()}
      />

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

/**
 * 图片历史（HISTORY-IMAGE-TAB-UI-0001）——并进左侧「图片历史」的归一 API（GET /history/images?category=）+ 6 分类：
 * 全部图片 / 图片生成·修改 / 电商·白底图 / 电商·模特图 / 电商·详情图 / 封面（顺序照冻结文档）。「全部图片」= 不传
 * category。旧「全部/仅封面」两 Chip 被这 6 个分类取代（封面成第 6 分类，不再是叠加筛选）。点图→大图、查看详情→
 * 详情弹窗、删除→硬删，均由 HistoryGrid 承载。key 随分类变更 → 切分类时 HistoryGrid 全新实例。Exported 供单测。
 */
const IMAGE_CATEGORY_CHIPS: { key: HistoryCategory | "all"; label: string }[] = [
  { key: "all", label: copy.historyImages.catAll },
  { key: "image_gen", label: copy.historyImages.tabImageGen },
  { key: "ecom_white", label: copy.historyImages.tabEcomWhite },
  { key: "ecom_model", label: copy.historyImages.tabEcomModel },
  { key: "ecom_detail", label: copy.historyImages.tabEcomDetail },
  { key: "cover", label: copy.historyImages.catCover }
];
export function PhotoHistory() {
  const [cat, setCat] = useState<HistoryCategory | "all">("all");
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {IMAGE_CATEGORY_CHIPS.map((c) => (
          <Chip key={c.key} selected={cat === c.key} onClick={() => setCat(c.key)} className="px-3 py-1.5 text-[12.5px]">
            {c.label}
          </Chip>
        ))}
      </div>
      <HistoryGrid key={cat} category={cat === "all" ? undefined : cat} />
    </div>
  );
}

/**
 * 历史生成 — 6 tabs：数字人 / 电商 / 视频生成 / 图片(6 分类) / 文案 / 提示词反推(图片·视频二级分类)。
 * onApplyPrefill：反推历史「带入生成」把载荷冒泡给 page（page.tsx 的 injectPrefill → setPendingPrefill + activate
 * → 目标表单 useEffect 同步消费 → clearPrefill）。签名与 ReversePromptForm 的同名 prop 一致，保持全库「冒泡
 * prefill」惯例；可选性使既有 page.test.tsx 的 stub 不破。
 */
export function GenerationHistory({ onApplyPrefill }: { onApplyPrefill?: (prefill: WorkbenchPrefill) => void } = {}) {
  return (
    <Card animateIn>
      {/* LABEL-TOGGLE-UI-0001：移除面板级全局「已含 AI 标识」恒显；改为每卡片按任务 apply_visible_label 显示。 */}
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
          <TabsTrigger value="video_gen" className={tabTriggerClass}>
            <Clapperboard size={14} strokeWidth={1.8} /> {copy.history.tabVideoGen}
          </TabsTrigger>
          <TabsTrigger value="photo" className={tabTriggerClass}>
            <Images size={14} strokeWidth={1.8} /> {copy.history.tabPhoto}
          </TabsTrigger>
          <TabsTrigger value="copywriting" className={tabTriggerClass}>
            <FileText size={14} strokeWidth={1.8} /> {copy.history.tabCopy}
          </TabsTrigger>
          {/* 第 6 tab（HISTORY-VIDEO-REVERSE-UI-0001）：图片/视频反推的二级分类在**该 tab 内部**（chip），
              不在此处加两个顶层 tab —— 用户明确强调过这点。 */}
          <TabsTrigger value="reverse_prompt" className={tabTriggerClass}>
            <ScanSearch size={14} strokeWidth={1.8} /> {copy.history.tabReverse}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="avatar_talk" className="outline-none">
          <HistoryList mode="avatar_talk" />
        </TabsContent>
        <TabsContent value="seedance_i2v" className="outline-none">
          <HistoryList mode="seedance_i2v" />
        </TabsContent>
        <TabsContent value="video_gen" className="outline-none">
          <HistoryList mode="video_gen" />
        </TabsContent>
        <TabsContent value="photo" className="outline-none">
          <PhotoHistory />
        </TabsContent>
        <TabsContent value="copywriting" className="outline-none">
          <CopyDraftList />
        </TabsContent>
        <TabsContent value="reverse_prompt" className="outline-none">
          <ReverseHistoryList onApplyPrefill={onApplyPrefill} />
        </TabsContent>
      </Tabs>
    </Card>
  );
}
