import { apiFetch } from "@/lib/api/client";
import type { ScriptGenerateResponse } from "@/lib/api/types";

export function generateScript(topic: string): Promise<ScriptGenerateResponse> {
  return apiFetch<ScriptGenerateResponse>("/api/v1/scripts/generate", { method: "POST", body: { topic } });
}
