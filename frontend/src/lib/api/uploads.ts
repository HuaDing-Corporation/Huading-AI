import { API_BASE_URL, ApiError, authHeaders } from "@/lib/api/client";
import { authStore } from "@/lib/auth/store";
import type { ApiResponse, UploadImageResponse } from "@/lib/api/types";

// Client-side guards (the backend enforces the same; this is a fast first pass).
export const ALLOWED_UPLOAD_TYPES = ["image/jpeg", "image/png", "image/webp"];
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024; // 10 MB

/**
 * Upload a product image (multipart) and get back the tenant-relative key used
 * as `image_key` for seedance_i2v. Uses a raw fetch (not apiFetch) because the
 * body is FormData — the browser must set the multipart Content-Type/boundary,
 * so we only attach the auth headers.
 */
export async function uploadImage(file: File): Promise<UploadImageResponse> {
  const form = new FormData();
  form.append("file", file);

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/api/v1/uploads/images`, {
      method: "POST",
      headers: authHeaders(),
      body: form
    });
  } catch {
    throw new ApiError("网络连接失败，请检查后端服务是否在线。", "NETWORK_ERROR", 0);
  }

  if (res.status === 401 && authStore.get()) authStore.clear();

  let payload: ApiResponse<UploadImageResponse> | null = null;
  try {
    payload = (await res.json()) as ApiResponse<UploadImageResponse>;
  } catch {
    payload = null;
  }

  if (!res.ok || payload?.error) {
    const err = payload?.error;
    throw new ApiError(
      err?.message ?? `上传失败（${res.status}）`,
      err?.code ?? "UPLOAD_ERROR",
      res.status,
      err?.detail
    );
  }

  return payload?.data as UploadImageResponse;
}
