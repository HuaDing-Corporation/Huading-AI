import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchMe } from "@/lib/api/auth";
import { meKey, quotaKey, videoKeys } from "@/lib/api/keys";
import { uploadImage } from "@/lib/api/uploads";
import { createVideo, getVideo, listVideos } from "@/lib/api/videos";
import type { CreateVideoRequest } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-context";

/** Provisional quota shape — no backend endpoint yet. */
export interface Quota {
  used: number;
  limit: number;
  resetAt: string;
}

export function useVideos() {
  const { session } = useAuth();
  return useQuery({ queryKey: videoKeys.list(), queryFn: listVideos, enabled: !!session });
}

export function useVideo(id: string | undefined) {
  const { session } = useAuth();
  return useQuery({
    queryKey: videoKeys.detail(id ?? ""),
    queryFn: () => getVideo(id as string),
    enabled: !!session && !!id
  });
}

export function useCreateVideo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateVideoRequest) => createVideo(input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: videoKeys.list() });
    }
  });
}

export function useUploadImage() {
  return useMutation({ mutationFn: (file: File) => uploadImage(file) });
}

export function useMe() {
  const { session } = useAuth();
  return useQuery({ queryKey: meKey, queryFn: fetchMe, enabled: !!session });
}

// TODO(#backend quota endpoint): wire queryFn to GET /api/v1/quota when it ships.
export function useQuota() {
  const { session } = useAuth();
  return useQuery<Quota | null>({
    queryKey: quotaKey,
    queryFn: async () => null,
    enabled: !!session
  });
}
