import { apiFetch } from "@/lib/api/client";
import { billingHeaders } from "@/lib/api/billing";
import type {
  BillingConfirmation,
  BillingQuote,
  ScriptGenerateRequest,
  ScriptGenerateResponse
} from "@/lib/api/types";

/** Fetch the authoritative quote bound to the exact script request body. */
export function estimateScript(params: ScriptGenerateRequest): Promise<BillingQuote> {
  return apiFetch<BillingQuote>("/api/v1/scripts/estimate", { method: "POST", body: params });
}

/** Generate the 口播文案. 电商带货 passes video_mode + duration_sec so the script
 *  length tracks the chosen duration (video == subtitle alignment). undefined
 *  fields are dropped by JSON.stringify, so avatar still sends just { topic }. */
export function generateScript(
  params: ScriptGenerateRequest,
  confirmation: BillingConfirmation
): Promise<ScriptGenerateResponse> {
  return apiFetch<ScriptGenerateResponse>("/api/v1/scripts/generate", {
    method: "POST",
    body: params,
    headers: billingHeaders(confirmation)
  });
}
