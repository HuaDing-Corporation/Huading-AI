import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchMe } from "@/lib/api/auth";
import { listAvatarPresets } from "@/lib/api/avatars";
import { avatarPresetsKey, copyKeys, coverKeys, meKey, quotaKey, subtitleTemplatesKey, videoKeys, voicesKey } from "@/lib/api/keys";
import { getQuota } from "@/lib/api/quota";
import { clearCopyDrafts, deleteCopyDraft, generateTitles, generateTopics, listCopyDraftsPage, rewriteCopy, saveCopyDraft } from "@/lib/api/copy";
import { generateScript } from "@/lib/api/scripts";
import { uploadImage, uploadProductImage } from "@/lib/api/uploads";
import { listVoices } from "@/lib/api/voices";
import { listSubtitleTemplates } from "@/lib/api/oral";
import { createCoverFromFrame, getFrameCandidates } from "@/lib/api/covers";
import { clearVideos, createVideo, deleteVideo, estimateVideo, generateScenePrompt, getVideo, listVideos, listVideosPage } from "@/lib/api/videos";
import type {
  CopyDraftCreateRequest,
  CopyRewriteRequest,
  CopyTitlesRequest,
  CopyTopicsRequest,
  CoverFromFrameRequest,
  CreateVideoRequest,
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
