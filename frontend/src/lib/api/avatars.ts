import { apiFetch } from "@/lib/api/client";
import type { AvatarPreset } from "@/lib/api/types";

export async function listAvatarPresets(): Promise<AvatarPreset[]> {
  const res = await apiFetch<{ items: AvatarPreset[]; total: number }>("/api/v1/avatars/presets", { method: "GET" });
  return res?.items ?? [];
}
