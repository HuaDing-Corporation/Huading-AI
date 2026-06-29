import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchMe } from "@/lib/api/auth";
import { listAvatarPresets } from "@/lib/api/avatars";
import { avatarPresetsKey, brandVoiceKeys, copyKeys, coverKeys, ecomModelStylesKey, ecomPosterTemplatesKey, labelSettingsKey, meKey, publishKeys, quotaKey, subtitleTemplatesKey, videoKeys, voicesKey } from "@/lib/api/keys";
import { getQuota } from "@/lib/api/quota";
import { clearCopyDrafts, deleteCopyDraft, generateTitles, generateTopics, listCopyDraftsPage, rewriteCopy, saveCopyDraft } from "@/lib/api/copy";
import { generateScript } from "@/lib/api/scripts";
import { uploadImage, uploadProductImage } from "@/lib/api/uploads";
import { listVoices } from "@/lib/api/voices";
import { listSubtitleTemplates } from "@/lib/api/oral";
import { createCoverFromFrame, getFrameCandidates } from "@/lib/api/covers";
import { cutoutImage, cutoutImageBatch, listModelStyles, listPosterTemplates, modelImage, modelImageBatch, posterImage, posterImageBatch } from "@/lib/api/ecom-images";
import { createBrandVoiceFromAudio, deleteBrandVoice, listBrandVoices } from "@/lib/api/brand-voices";
import { getLabelSettings, updateLabelSettings } from "@/lib/api/label-settings";
import { createPublishDrafts, deletePublishRecord, listPublishPlatforms, listPublishRecords, markPublished } from "@/lib/api/publish";
import { clearVideos, createVideo, deleteVideo, estimateVideo, generateScenePrompt, getVideo, listVideos, listVideosPage } from "@/lib/api/videos";
import type {
  CopyDraftCreateRequest,
  CopyRewriteRequest,
  CopyTitlesRequest,
  CopyTopicsRequest,
  CoverFromFrameRequest,
  CreateVideoRequest,
  CreateBrandVoiceInput,
  CreateDraftsRequest,
  CutoutBatchRequest,
  CutoutRequest,
  LabelSettingsUpdate,
  ModelBatchRequest,
  ModelRequest,
  PosterBatchRequest,
  PosterRequest,
  ScriptGenerateRequest
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
export function useUploadProductImage() {
  return useMutation({ mutationFn: (file: File) => uploadProductImage(file) });
}
export function useScriptGenerate() {
  return useMutation({ mutationFn: (params: ScriptGenerateRequest) => generateScript(params) });
}
export function useScenePromptGenerate() {
  return useMutation({ mutationFn: (topic: string) => generateScenePrompt(topic) });
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
export function useCutoutImage() {
  return useMutation({ mutationFn: (body: CutoutRequest) => cutoutImage(body) });
}
export function useCutoutBatch() {
  return useMutation({ mutationFn: (body: CutoutBatchRequest) => cutoutImageBatch(body) });
}
// ── 电商图扩展 Phase2 (ECOM-MODEL-UI-0001) — AI 模特风格预设 + 单张/批量生成 ──
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
// 列表：有 processing 项时每 3s 轮询，全部终态(ready/failed)则停轮询。
export function useBrandVoices() {
  const { session } = useAuth();
  return useQuery({
    queryKey: brandVoiceKeys.list(),
    queryFn: listBrandVoices,
    enabled: !!session,
    refetchInterval: (query) => (query.state.data?.some((v) => v.status === "processing") ? 3000 : false)
  });
}
export function useCreateBrandVoice() {
  const qc = useQueryClient();
  return useMutation({
    // 三段式编排：上传音频 → JSON 创建(带 consent_confirmed)。
    mutationFn: (input: CreateBrandVoiceInput) => createBrandVoiceFromAudio(input),
    // 新建后失效品牌音色列表 + voices(ready 克隆音色会进口播 picker)。
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: brandVoiceKeys.all });
      void qc.invalidateQueries({ queryKey: voicesKey });
    }
  });
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
    mutationFn: (id: string) => markPublished(id),
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
