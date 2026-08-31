import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchMe } from "@/lib/api/auth";
import {
  adjustTenantCredits,
  assignVoiceSlot,
  changeTenantPlan,
  changeTenantStatus,
  fetchAdminAudit,
  fetchAdminTasks,
  fetchAdminTenantDetail,
  fetchAdminTenants,
  fetchAdminUsage,
  fetchAdminVoiceSlots,
  getAdminBrandVoiceOrder,
  listAdminBrandVoiceOrders,
  resolveAdminBrandVoiceOrder,
  retryAdminTask,
  type AdminTaskFamily,
  type AdminTaskStatus,
  type AdminTenantListQuery,
  type AdminUsageQuery,
  type AuditAction,
  type PlanCode
} from "@/lib/api/admin-console";
import { listAvatarPresets } from "@/lib/api/avatars";
import { adminBrandVoiceOrderKeys, analyticsKeys, avatarPresetsKey, batchKeys, bgmLibraryKey, brandVoiceKeys, brandVoiceOrderKeys, copyKeys, coverKeys, ecomModelStylesKey, ecomPosterTemplatesKey, historyImageKeys, labelSettingsKey, meKey, publishKeys, quotaKey, reversePromptKeys, subtitleTemplatesKey, videoKeys, voicesKey } from "@/lib/api/keys";
import {
  clearHistoryImages,
  deleteHistoryImageSet,
  getHistoryImageSet,
  listHistoryImages,
  type HistoryCategory
} from "@/lib/api/history-images";
import { fetchAnalyticsByProvider, fetchAnalyticsByTenant, fetchAnalyticsOverview, fetchAnalyticsTimeseries, type AnalyticsRange } from "@/lib/api/analytics";
import { cancelBatch, createBatch, estimateBatch, getBatch, listBatches } from "@/lib/api/batches";
import { getQuota } from "@/lib/api/quota";
import { clearCopyDrafts, deleteCopyDraft, estimateCopy, generateTitles, generateTopics, listCopyDraftsPage, rewriteCopy, saveCopyDraft } from "@/lib/api/copy";
import { generateScript } from "@/lib/api/scripts";
import {
  clearReversePromptJobs,
  deleteReversePromptJob,
  estimateReversePrompt,
  getReversePromptJob,
  listReversePromptJobs,
  regenerateReversePrompt,
  reverseFromAsset,
  saveReversePrompt,
  type ReverseClearScope,
  type ReverseFromAssetInput,
  type ReverseSourceKind
} from "@/lib/api/reverse-prompt";
import { uploadAvatarVideo, uploadImage, uploadProductImage, uploadReverseVideo, uploadVideoGenReference } from "@/lib/api/uploads";
import { listBgmLibrary } from "@/lib/api/bgm";
import { uploadAudio } from "@/lib/api/brand-voices";
import { listVoices } from "@/lib/api/voices";
import { listSubtitleTemplates } from "@/lib/api/oral";
import { createCoverFromFrame, getFrameCandidates } from "@/lib/api/covers";
import {
  createEcomCutout,
  createEcomModel,
  cutoutImage,
  cutoutImageBatch,
  estimateEcomCutout,
  estimateEcomModel,
  listModelStyles,
  listPosterTemplates,
  modelImage,
  modelImageBatch,
  posterImage,
  posterImageBatch
} from "@/lib/api/ecom-images";
import { deleteBrandVoice, listBrandVoices } from "@/lib/api/brand-voices";
import { listBrandVoiceOrders } from "@/lib/api/brand-voice-orders";
import { getLabelSettings, updateLabelSettings } from "@/lib/api/label-settings";
import { createPublishDrafts, deletePublishRecord, listPublishPlatforms, listPublishRecords, markPublished } from "@/lib/api/publish";
import { clearVideos, createVideo, deleteVideo, estimateVideo, generateScenePrompt, getVideo, listVideos, listVideosPage } from "@/lib/api/videos";
import type {
  BatchRequest,
  BillingConfirmation,
  CopyDraftCreateRequest,
  CopyRewriteRequest,
  CopyTitlesRequest,
  CopyTopicsRequest,
  CoverFromFrameRequest,
  CreateVideoRequest,
  CreateDraftsRequest,
  PublishPlatformId,
  CutoutBatchRequest,
  CutoutBatchResponse,
  CutoutRequest,
  CutoutResponse,
  LabelSettingsUpdate,
  ModelBatchRequest,
  ModelBatchResponse,
  ModelRequest,
  ModelResponse,
  PosterBatchRequest,
  PosterRequest,
  ScenePromptRequest,
  ScriptGenerateRequest,
  AnalyticsGranularity,
  AnalyticsTenantSort
} from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-context";

export function useVideos() {
  const { session } = useAuth();
  return useQuery({ queryKey: videoKeys.list(), queryFn: listVideos, enabled: !!session });
}
export function useVideoHistory(mode: string, kind?: string) {
  const { session } = useAuth();
  return useInfiniteQuery({
    queryKey: videoKeys.history(mode, kind),
    queryFn: ({ pageParam }) => listVideosPage({ mode, kind, limit: 10, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, page) => sum + page.items.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
    enabled: !!session
  });
}
export function useVideo(id: string | undefined) {
  const { session } = useAuth();
  return useQuery({ queryKey: videoKeys.detail(id ?? ""), queryFn: () => getVideo(id as string), enabled: !!session && !!id });
}
// 图片历史·统一模块：按 category 分页拉列表（page 从 1 起，累计已加载数 < total 才有下一页）。
// category 省略（HISTORY-IMAGE-TAB-UI-0001）= 全部图片（不传 category → BE 返回全部分类混合，按时间倒序）。
export function useHistoryImages(category?: HistoryCategory) {
  const { session } = useAuth();
  return useInfiniteQuery({
    queryKey: historyImageKeys.list(category),
    queryFn: ({ pageParam }) => listHistoryImages({ category, page: pageParam }),
    initialPageParam: 1,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, page) => sum + page.items.length, 0);
      return loaded < lastPage.total ? allPages.length + 1 : undefined;
    },
    enabled: !!session
  });
}
// 重开整套：仅弹窗打开（id 存在）时拉详情。
export function useHistoryImageSet(category: HistoryCategory | string, id: string | undefined) {
  const { session } = useAuth();
  return useQuery({
    queryKey: historyImageKeys.detail(category, id ?? ""),
    queryFn: () => getHistoryImageSet(category, id as string),
    enabled: !!session && !!id
  });
}
// ── 图片历史删除（HISTORY-CHAT-DELETE-UI-0001）──────────────────────────────
// FIX1 曾摘掉（#175 同步删媒体的 P1 群）；本包按冻结 §二复活为**纯记录软删**（不碰媒体/Asset）。
// 成功后失效 historyImageKeys.all（列表 + 详情前缀）：**不做乐观移除**——承重门 5 要求「绝不留一个
// 看起来删了其实没删的界面」，失败即保持原样 + 弹窗内报错，成功才由 refetch 让它消失。
export function useDeleteHistoryImageSet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ category, id }: { category: HistoryCategory | string; id: string }) =>
      deleteHistoryImageSet(category, id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: historyImageKeys.all })
  });
}
// 清空**当前分类**（E3，单事务批量软删）。同样失效全部图片历史键：其余分类的计数由 BE 保证不变，
// 前端只需重取（承重门 2 断言其余分类一条不少）。
export function useClearHistoryImages() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (category: HistoryCategory | string) => clearHistoryImages(category),
    onSuccess: () => void qc.invalidateQueries({ queryKey: historyImageKeys.all })
  });
}
export function useCreateVideo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateVideoRequest) => createVideo(input),
    onSuccess: () => void qc.invalidateQueries({ queryKey: videoKeys.list() })
  });
}
// ── 历史删除 / 清空 (HIST-UI-0001) — 成功后失效所有 video 查询(前缀)使历史列表刷新 ──
export function useDeleteVideo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteVideo(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: videoKeys.all })
  });
}
export function useClearVideos() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mode: string) => clearVideos(mode),
    onSuccess: () => void qc.invalidateQueries({ queryKey: videoKeys.all })
  });
}
export function useEstimateVideo() {
  return useMutation({ mutationFn: (input: CreateVideoRequest) => estimateVideo(input) });
}
export function useUploadImage() {
  return useMutation({ mutationFn: (file: File) => uploadImage(file) });
}
// 视频反推 source 上传（VIDEO-REVERSE-PROMPT-UI-0001）→ asset_id（作 reverseFromAsset 的 source_asset_id）。
export function useUploadReverseVideo() {
  return useMutation({ mutationFn: (file: File) => uploadReverseVideo(file) });
}
export function useUploadProductImage() {
  return useMutation({ mutationFn: (file: File) => uploadProductImage(file) });
}
// 数字人·本人出镜视频源上传（AVATAR-VIDEO-SOURCE-UI-0001）→ asset_id（作 avatar_video_asset_id）。
export function useUploadAvatarVideo() {
  return useMutation({ mutationFn: (file: File) => uploadAvatarVideo(file) });
}
// 视频生成·参考视频上传（VIDEO-GEN-V2V-UI-0001，purpose=video_gen_reference）→ asset_id（作 reference_video_asset_ids 元素）。
export function useUploadVideoGenReference() {
  return useMutation({ mutationFn: (file: File) => uploadVideoGenReference(file) });
}
// 视频生成 BGM 上传（VIDEOGEN-UI-0001）：复用 /uploads/audio（声音克隆已建）→ asset_id。
export function useUploadAudio() {
  return useMutation({ mutationFn: (audio: Blob) => uploadAudio(audio) });
}
export function useScriptGenerate() {
  return useMutation({
    mutationFn: ({
      params,
      confirmation
    }: {
      params: ScriptGenerateRequest;
      confirmation: BillingConfirmation;
    }) => generateScript(params, confirmation)
  });
}
export function useScenePromptGenerate() {
  return useMutation({
    mutationFn: ({
      params,
      confirmation
    }: {
      params: ScenePromptRequest;
      confirmation: BillingConfirmation;
    }) => generateScenePrompt(params, confirmation)
  });
}
// ── 提示词反推 (REVERSE-PROMPT-UI-0001) — 预估 / 反推 / 重推 / 保存 ──
/**
 * 计费预估（§八 M4）——计费门弹窗打开时调，展示 BE 返回的档位金额。
 * 用 mutation 而非 query：它是「打开弹窗」这个动作触发的一次性取数，且**失败必须显式可见**
 *（query 的缓存/重试会让「这次到底估没估到」变得含糊，而这里估不到就不许提交）。
 */
export function useEstimateReversePrompt() {
  return useMutation({ mutationFn: (input: ReverseFromAssetInput) => estimateReversePrompt(input) });
}
export function useReverseFromAsset() {
  return useMutation({ mutationFn: (input: ReverseFromAssetInput) => reverseFromAsset(input) });
}
export function useRegenerateReversePrompt() {
  return useMutation({ mutationFn: (jobId: string) => regenerateReversePrompt(jobId) });
}
export function useSaveReversePrompt() {
  return useMutation({ mutationFn: (jobId: string) => saveReversePrompt(jobId) });
}
// ── 反推历史 (HISTORY-VIDEO-REVERSE-UI-0001) — 列表(分页) / 详情(惰性) / 软删 ──
// ⚠️ BE 是 **page/page_size 制**（routes/reverse_prompt.py:74-75），与 useHistoryImages 同构；
//    不是 useVideoHistory 那套 limit/offset —— 别抄错。source_kind 省略 = 全部。
export function useReversePromptJobs(sourceKind?: ReverseSourceKind) {
  const { session } = useAuth();
  return useInfiniteQuery({
    queryKey: reversePromptKeys.list(sourceKind),
    queryFn: ({ pageParam }) => listReversePromptJobs({ source_kind: sourceKind, page: pageParam }),
    initialPageParam: 1,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, page) => sum + page.items.length, 0);
      return loaded < lastPage.total ? allPages.length + 1 : undefined;
    },
    enabled: !!session
  });
}
// 详情：列表项不含 result（BE schemas:97-103 只有 6 字段）→ 「看详情 / 带入生成」都必须先取详情拿 fill_targets。
// 仅在弹窗打开（id 存在）时才拉，与 useHistoryImageSet 同惯例（关闭态不发请求）。
export function useReversePromptJob(id: string | undefined) {
  const { session } = useAuth();
  return useQuery({
    queryKey: reversePromptKeys.detail(id ?? ""),
    queryFn: () => getReversePromptJob(id as string),
    enabled: !!session && !!id
  });
}
// 软删：BE 只置 deleted_at、不碰媒体 → 成功后失效列表缓存（详情键不动：BE 软删后详情仍可取）。
export function useDeleteReversePromptJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => deleteReversePromptJob(jobId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: reversePromptKeys.all });
    }
  });
}
// 清空反推历史（FIX1 范围2）：scope 必传、跟随当前筛选。同样只失效反推键——不做乐观移除
// （失败即保持原样 + 弹窗内报错，成功由 refetch 让它消失；与 #221/#223 同一套范式）。
export function useClearReversePromptJobs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scope: ReverseClearScope) => clearReversePromptJobs(scope),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: reversePromptKeys.all });
    }
  });
}
// ── 口播生产力增强 (ORAL-PROD-UI-0001) — 字幕模板 / 封面截帧 ──
export function useSubtitleTemplates() {
  const { session } = useAuth();
  return useQuery({ queryKey: subtitleTemplatesKey, queryFn: listSubtitleTemplates, enabled: !!session });
}
export function useFrameCandidates(videoTaskId: string | undefined, count = 5) {
  const { session } = useAuth();
  return useQuery({
    queryKey: coverKeys.frameCandidates(videoTaskId ?? "", count),
    queryFn: () => getFrameCandidates(videoTaskId as string, count),
    enabled: !!session && !!videoTaskId
  });
}
export function useCoverFromFrame() {
  // 截帧封面是 Asset 挂口播任务，不进 photo VideoTask 历史，故不失效图片历史(FIX1)。
  return useMutation({ mutationFn: (body: CoverFromFrameRequest) => createCoverFromFrame(body) });
}
// ── 电商图扩展 Phase1 (ECOM-IMG-UI-0001) — 白底图/抠图(单张 + 批量 fan-out)──
export function useEstimateEcomCutout() {
  return useMutation({
    mutationFn: (body: CutoutRequest | CutoutBatchRequest) => estimateEcomCutout(body)
  });
}
export function useCreateEcomCutout() {
  return useMutation({
    mutationFn: ({
      body,
      confirmation
    }: {
      body: CutoutRequest | CutoutBatchRequest;
      confirmation: BillingConfirmation;
    }): Promise<CutoutResponse | CutoutBatchResponse> =>
      "items" in body
        ? createEcomCutout(body, confirmation)
        : createEcomCutout(body, confirmation)
  });
}
export function useCutoutImage() {
  return useMutation({ mutationFn: (body: CutoutRequest) => cutoutImage(body) });
}
export function useCutoutBatch() {
  return useMutation({ mutationFn: (body: CutoutBatchRequest) => cutoutImageBatch(body) });
}
// ── 电商图扩展 Phase2 (ECOM-MODEL-UI-0001) — AI 模特风格预设 + 单张/批量生成 ──
export function useEstimateEcomModel() {
  return useMutation({
    mutationFn: (body: ModelRequest | ModelBatchRequest) => estimateEcomModel(body)
  });
}
export function useCreateEcomModel() {
  return useMutation({
    mutationFn: ({
      body,
      confirmation
    }: {
      body: ModelRequest | ModelBatchRequest;
      confirmation: BillingConfirmation;
    }): Promise<ModelResponse | ModelBatchResponse> =>
      "items" in body
        ? createEcomModel(body, confirmation)
        : createEcomModel(body, confirmation)
  });
}
export function useModelStyles() {
  const { session } = useAuth();
  return useQuery({ queryKey: ecomModelStylesKey, queryFn: listModelStyles, enabled: !!session });
}
export function useModelImage() {
  return useMutation({ mutationFn: (body: ModelRequest) => modelImage(body) });
}
export function useModelBatch() {
  return useMutation({ mutationFn: (body: ModelBatchRequest) => modelImageBatch(body) });
}
// ── 电商图扩展 Phase3 (ECOM-POSTER-UI-0001) — 营销海报版式预设 + 单张/批量生成 ──
export function usePosterTemplates() {
  const { session } = useAuth();
  return useQuery({ queryKey: ecomPosterTemplatesKey, queryFn: listPosterTemplates, enabled: !!session });
}
export function usePosterImage() {
  return useMutation({ mutationFn: (body: PosterRequest) => posterImage(body) });
}
export function usePosterBatch() {
  return useMutation({ mutationFn: (body: PosterBatchRequest) => posterImageBatch(body) });
}
// ── 品牌音色 / 声音克隆 (BRAND-VOICE-UI-0001) ──
// 只有 CosyVoice 自动创建的 processing 项每 3s 轮询；Doubao 人工订单绝不伪装成供应商轮询。
export function useBrandVoices() {
  const { session } = useAuth();
  return useQuery({
    queryKey: brandVoiceKeys.list(),
    queryFn: listBrandVoices,
    enabled: !!session,
    refetchInterval: (query) =>
      query.state.data?.some(
        (voice) => voice.provider === "cosyvoice-voice-clone" && voice.status === "processing"
      )
        ? 3000
        : false
  });
}
export function useBrandVoiceOrders() {
  const { session } = useAuth();
  return useQuery({ queryKey: brandVoiceOrderKeys.list(), queryFn: listBrandVoiceOrders, enabled: !!session });
}
export function useDeleteBrandVoice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteBrandVoice(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: brandVoiceKeys.all });
      void qc.invalidateQueries({ queryKey: voicesKey });
    }
  });
}
// ── 深度合成标识设置 (LABEL-UI-0001) ──
export function useLabelSettings() {
  const { session } = useAuth();
  return useQuery({ queryKey: labelSettingsKey, queryFn: getLabelSettings, enabled: !!session });
}
export function useUpdateLabelSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: LabelSettingsUpdate) => updateLabelSettings(body),
    onSuccess: (data) => qc.setQueryData(labelSettingsKey, data)
  });
}
// ── 发布中心 (PUBLISH-UI-0001) ──
export function usePublishPlatforms() {
  const { session } = useAuth();
  return useQuery({ queryKey: publishKeys.platforms(), queryFn: listPublishPlatforms, enabled: !!session });
}
export function usePublishRecords() {
  const { session } = useAuth();
  return useQuery({ queryKey: publishKeys.records(), queryFn: listPublishRecords, enabled: !!session });
}
export function useCreatePublishDrafts() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateDraftsRequest) => createPublishDrafts(body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: publishKeys.records() })
  });
}
export function useMarkPublished() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ recordId, platformId }: { recordId: string; platformId: PublishPlatformId }) =>
      markPublished(recordId, platformId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: publishKeys.records() })
  });
}
export function useDeletePublishRecord() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deletePublishRecord(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: publishKeys.records() })
  });
}
// ── 文案仿写 + 标题/话题生成 (COPY-UI-0001) — 同步 mutation；草稿列表 infinite query ──
export function useEstimateCopy() {
  const { session } = useAuth();
  return useQuery({ queryKey: copyKeys.estimate(session?.tenantId), queryFn: estimateCopy, enabled: !!session?.tenantId });
}
export function useRewriteCopy() {
  return useMutation({ mutationFn: (params: CopyRewriteRequest) => rewriteCopy(params) });
}
export function useGenerateTitles() {
  return useMutation({ mutationFn: (params: CopyTitlesRequest) => generateTitles(params) });
}
export function useGenerateTopics() {
  return useMutation({ mutationFn: (params: CopyTopicsRequest) => generateTopics(params) });
}
export function useSaveCopyDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: CopyDraftCreateRequest) => saveCopyDraft(params),
    onSuccess: () => void qc.invalidateQueries({ queryKey: copyKeys.drafts() })
  });
}
export function useDeleteCopyDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteCopyDraft(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: copyKeys.drafts() })
  });
}
export function useClearCopyDrafts() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => clearCopyDrafts(),
    onSuccess: () => void qc.invalidateQueries({ queryKey: copyKeys.drafts() })
  });
}
export function useCopyDrafts() {
  const { session } = useAuth();
  return useInfiniteQuery({
    queryKey: copyKeys.drafts(),
    queryFn: ({ pageParam }) => listCopyDraftsPage({ limit: 10, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, page) => sum + page.items.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
    enabled: !!session
  });
}
export function useVoices() {
  const { session } = useAuth();
  return useQuery({ queryKey: voicesKey, queryFn: listVoices, enabled: !!session });
}
// 配乐库（VIDEOGEN-UI-0001）：登录后拉取，供视频生成 BGM 选择 + 试听。
export function useBgmLibrary() {
  const { session } = useAuth();
  return useQuery({ queryKey: bgmLibraryKey, queryFn: listBgmLibrary, enabled: !!session });
}
export function useAvatarPresets() {
  const { session } = useAuth();
  return useQuery({ queryKey: avatarPresetsKey, queryFn: listAvatarPresets, enabled: !!session });
}
export function useMe() {
  const { session } = useAuth();
  return useQuery({ queryKey: meKey, queryFn: fetchMe, enabled: !!session });
}
export function useQuota() {
  const { session } = useAuth();
  return useQuery({ queryKey: quotaKey, queryFn: getQuota, enabled: !!session });
}

export function useAdminBrandVoiceOrders(status: import("@/lib/api/admin-console").AdminBrandVoiceOrderStatus | "", page = 1) {
  return useQuery({
    queryKey: adminBrandVoiceOrderKeys.list(status, page, 20),
    queryFn: () => listAdminBrandVoiceOrders({ status, page, page_size: 20 })
  });
}

export function useAdminBrandVoiceOrder(orderId: string | null) {
  return useQuery({
    queryKey: adminBrandVoiceOrderKeys.detail(orderId ?? ""),
    queryFn: () => getAdminBrandVoiceOrder(orderId as string),
    enabled: !!orderId
  });
}

export function useResolveAdminBrandVoiceOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ orderId, action }: { orderId: string; action: import("@/lib/api/admin-console").AdminBrandVoiceOrderAction }) =>
      resolveAdminBrandVoiceOrder(orderId, action),
    onSuccess: (result) => {
      qc.setQueryData(adminBrandVoiceOrderKeys.detail(result.id), result);
      void qc.invalidateQueries({ queryKey: adminBrandVoiceOrderKeys.all });
    }
  });
}

// ── 批量生产中心 (BATCH-PROD-UI-0001) ──
export function useEstimateBatch() {
  return useMutation({ mutationFn: (input: BatchRequest) => estimateBatch(input) });
}
export function useCreateBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: BatchRequest) => createBatch(input),
    onSuccess: () => void qc.invalidateQueries({ queryKey: batchKeys.list() })
  });
}
export function useBatches() {
  const { session } = useAuth();
  return useQuery({
    queryKey: batchKeys.list(),
    queryFn: () => listBatches(),
    enabled: !!session,
    // 有进行中批次则轮询(≥5s)，全终态停；后台标签页由 React Query 默认(refetchIntervalInBackground:false)暂停。
    refetchInterval: (query) => (query.state.data?.some((b) => b.status === "running") ? 5000 : false)
  });
}
export function useBatch(id: string | null) {
  const { session } = useAuth();
  return useQuery({
    queryKey: batchKeys.detail(id ?? ""),
    queryFn: () => getBatch(id as string),
    enabled: !!session && !!id,
    // 组视图轮询：子任务未全终态则每 5s；全 done/failed/cancelled 停。页面不活跃自动暂停。
    refetchInterval: (query) => {
      const tasks = query.state.data?.tasks ?? [];
      const active = tasks.some((t) => t.status === "queued" || t.status === "running");
      return active ? 5000 : false;
    }
  });
}
export function useCancelBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => cancelBatch(id),
    onSuccess: (_d, id) => {
      void qc.invalidateQueries({ queryKey: batchKeys.detail(id) });
      void qc.invalidateQueries({ queryKey: batchKeys.list() });
    }
  });
}

// ── 管理员数据看板 (ANALYTICS-UI-0001) ── enabled 仅 !!session（不按 admin 门控）：
// 让非管理员也真实发起请求 → 命中后端 403，由页面优雅处理，不靠前端隐藏兜底。
// enabled 参数：非法区间(from>to)时置 false，避免把非法区间发给后端触发 422（对齐 date-range-picker 契约）。
export function useAnalyticsOverview(range: AnalyticsRange, enabled = true) {
  const { session } = useAuth();
  return useQuery({
    queryKey: analyticsKeys.overview(range.from, range.to),
    queryFn: () => fetchAnalyticsOverview(range),
    enabled: !!session && enabled
  });
}

export function useAnalyticsByTenant(
  range: AnalyticsRange,
  opts: { sort: AnalyticsTenantSort; limit: number; offset: number },
  enabled = true
) {
  const { session } = useAuth();
  return useQuery({
    queryKey: analyticsKeys.byTenant(range.from, range.to, opts.sort, opts.limit, opts.offset),
    queryFn: () => fetchAnalyticsByTenant(range, opts),
    enabled: !!session && enabled
  });
}

export function useAnalyticsByProvider(range: AnalyticsRange, enabled = true) {
  const { session } = useAuth();
  return useQuery({
    queryKey: analyticsKeys.byProvider(range.from, range.to),
    queryFn: () => fetchAnalyticsByProvider(range),
    enabled: !!session && enabled
  });
}

export function useAnalyticsTimeseries(range: AnalyticsRange, granularity: AnalyticsGranularity, enabled = true) {
  const { session } = useAuth();
  return useQuery({
    queryKey: analyticsKeys.timeseries(range.from, range.to, granularity),
    queryFn: () => fetchAnalyticsTimeseries(range, granularity),
    enabled: !!session && enabled
  });
}

// ── 管理员后台 (ADMIN-CONSOLE-UI-0001) ── /admin/console/*。写操作成功后失效整棵 admin 前缀（列表 + 审计同刷，写全落审计）。
const adminKeys = {
  all: ["admin-console"] as const,
  tenants: (q: AdminTenantListQuery) => ["admin-console", "tenants", q] as const,
  tenantDetail: (id: string) => ["admin-console", "tenant", id] as const,
  voiceSlots: ["admin-console", "voice-slots"] as const,
  usage: (q: AdminUsageQuery) => ["admin-console", "usage", q] as const,
  tasks: (q: Record<string, unknown>) => ["admin-console", "tasks", q] as const,
  audit: (q: Record<string, unknown>) => ["admin-console", "audit", q] as const
};

export function useAdminTenants(query: AdminTenantListQuery) {
  const { session } = useAuth();
  return useQuery({ queryKey: adminKeys.tenants(query), queryFn: () => fetchAdminTenants(query), enabled: !!session });
}
export function useAdminTenantDetail(tenantId: string | null) {
  const { session } = useAuth();
  return useQuery({
    queryKey: adminKeys.tenantDetail(tenantId ?? ""),
    queryFn: () => fetchAdminTenantDetail(tenantId as string),
    enabled: !!session && !!tenantId
  });
}
export function useAdjustTenantCredits() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { tenantId: string; delta: number; reason: string }) =>
      adjustTenantCredits(input.tenantId, { delta: input.delta, reason: input.reason }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: adminKeys.all })
  });
}
export function useChangeTenantPlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { tenantId: string; planCode: PlanCode }) => changeTenantPlan(input.tenantId, input.planCode),
    onSuccess: () => void qc.invalidateQueries({ queryKey: adminKeys.all })
  });
}
export function useChangeTenantStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { tenantId: string; active: boolean }) => changeTenantStatus(input.tenantId, input.active),
    onSuccess: () => void qc.invalidateQueries({ queryKey: adminKeys.all })
  });
}
export function useAdminVoiceSlots() {
  const { session } = useAuth();
  return useQuery({ queryKey: adminKeys.voiceSlots, queryFn: fetchAdminVoiceSlots, enabled: !!session });
}
export function useAssignVoiceSlot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { tenantId: string; speaker_id: string }) => assignVoiceSlot(input.tenantId, { speaker_id: input.speaker_id }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: adminKeys.all })
  });
}
export function useAdminUsage(query: AdminUsageQuery) {
  const { session } = useAuth();
  return useQuery({ queryKey: adminKeys.usage(query), queryFn: () => fetchAdminUsage(query), enabled: !!session });
}
export function useAdminTasks(query: { task_family?: AdminTaskFamily | ""; tenant_id?: string; status?: AdminTaskStatus | ""; from?: string; to?: string; page: number; page_size: number }) {
  const { session } = useAuth();
  return useQuery({ queryKey: adminKeys.tasks(query), queryFn: () => fetchAdminTasks(query), enabled: !!session });
}
export function useRetryAdminTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { taskId: string; taskFamily?: AdminTaskFamily }) => retryAdminTask(input.taskId, input.taskFamily),
    onSuccess: () => void qc.invalidateQueries({ queryKey: adminKeys.all })
  });
}
export function useAdminAudit(query: { action?: AuditAction | ""; target_tenant_id?: string; page: number; page_size: number }) {
  const { session } = useAuth();
  return useQuery({ queryKey: adminKeys.audit(query), queryFn: () => fetchAdminAudit(query), enabled: !!session });
}
