import { apiFetch } from "@/lib/api/client";
import type { Quota } from "@/lib/api/types";

export function getQuota(): Promise<Quota> {
  return apiFetch<Quota>("/api/v1/quota", { method: "GET" });
}
