import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchMe } from "@/lib/api/auth";
import { listAvatarPresets } from "@/lib/api/avatars";
import { avatarPresetsKey, meKey, quotaKey, videoKeys, voicesKey } from "@/lib/api/keys";
import { getQuota } from "@/lib/api/quota";
import { generateScript } from "@/lib/api/scripts";
import { uploadImage, uploadProductImage } from "@/lib/api/uploads";
import { listVoices } from "@/lib/api/voices";
import { createVideo, getVideo, listVideos } from "@/lib/api/videos";
import type { CreateVideoRequest } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-context";

export function useVideos() {
  const { session } = useAuth();
  return useQuery({ queryKey: videoKeys.list(), queryFn: listVideos, enabled: !!session });
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
export function useUploadImage() {
  return useMutation({ mutationFn: (file: File) => uploadImage(file) });
}
export function useUploadProductImage() {
  return useMutation({ mutationFn: (file: File) => uploadProductImage(file) });
}
export function useScriptGenerate() {
  return useMutation({ mutationFn: (topic: string) => generateScript(topic) });
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
