import { ApiError } from "@/lib/api/client";
import { copy } from "@/lib/copy";

/**
 * Map a thrown error to user-facing text: surface the backend ApiError.message
 * (so users see the real reason, e.g. "Active subscription not found."), keep the
 * curated friendly copy for quota, and fall back to generic for a non-ApiError or
 * an empty message. Shared by the workbench forms (数字人口播 + 电商带货 i2v).
 */
export function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "tenant_quota_exceeded") return copy.errors.quota;
    return err.message || copy.errors.generic;
  }
  return copy.errors.generic;
}
