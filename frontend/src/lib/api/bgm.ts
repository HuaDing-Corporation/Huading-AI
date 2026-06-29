import { apiFetch } from "@/lib/api/client";
import type { BgmLibraryResponse, BgmTrack } from "@/lib/api/types";

/**
 * 配乐库 (VIDEOGEN-UI-0001, seam §3)：平台预置免版权 BGM，供视频生成 video_gen 选用 + 试听。
 * 沿用 apiFetch(鉴权/封套/ApiError)，零裸 fetch。BGM 上传走 /uploads/audio(复用 uploadAudio)。
 */
export async function listBgmLibrary(): Promise<BgmTrack[]> {
  const res = await apiFetch<BgmLibraryResponse>("/api/v1/bgm-library", { method: "GET" });
  return res?.items ?? [];
}
