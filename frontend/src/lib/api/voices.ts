import { apiFetch } from "@/lib/api/client";
import type { Voice } from "@/lib/api/types";

export async function listVoices(): Promise<Voice[]> {
  const res = await apiFetch<{ items: Voice[]; total: number }>("/api/v1/voices", { method: "GET" });
  return res?.items ?? [];
}
