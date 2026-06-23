import { apiFetch } from "@/lib/api/client";
import type { ScriptGenerateRequest, ScriptGenerateResponse } from "@/lib/api/types";

/** Generate the 口播文案. 电商带货 passes video_mode + duration_sec so the script
 *  length tracks the chosen duration (video == subtitle alignment). undefined
 *  fields are dropped by JSON.stringify, so avatar still sends just { topic }. */
export function generateScript(params: ScriptGenerateRequest): Promise<ScriptGenerateResponse> {
  return apiFetch<ScriptGenerateResponse>("/api/v1/scripts/generate", { method: "POST", body: params });
}
