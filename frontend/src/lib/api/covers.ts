import { apiFetch } from "@/lib/api/client";
import type {
  CoverFromFrameRequest,
  CoverFromFrameResponse,
  FrameCandidate,
  FrameCandidatesResponse
} from "@/lib/api/types";

/**
 * 封面制作 (ORAL-PROD-UI-0001) — 截帧法。同步 REST，沿用 apiFetch（解包 M2 封套、
 * 注入 Bearer + 租户头）。AI 封面不在此（复用 0003 文生图 purpose=cover）。
 */

/** 候选帧（截帧法第一步）— 等距采样 n 帧（clamp 1..10，默认 5）。 */
export async function getFrameCandidates(videoTaskId: string, count = 5): Promise<FrameCandidate[]> {
  const query = new URLSearchParams({ video_task_id: videoTaskId, count: String(count) });
  const res = await apiFetch<FrameCandidatesResponse>(
    `/api/v1/covers/frame-candidates?${query.toString()}`,
    { method: "GET" }
  );
  return res?.frames ?? [];
}

/** 截帧 + 标题叠加 → 封面（同步；产物入图片存储/历史 kind=cover）。 */
export function createCoverFromFrame(body: CoverFromFrameRequest): Promise<CoverFromFrameResponse> {
  return apiFetch<CoverFromFrameResponse>("/api/v1/covers/from-frame", { method: "POST", body });
}
