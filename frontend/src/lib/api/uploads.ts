import { API_BASE_URL, ApiError, authHeaders } from "@/lib/api/client";
import { authStore } from "@/lib/auth/store";
import type { ApiResponse, UploadImageResponse, UploadResponse } from "@/lib/api/types";

// Client-side guards (the backend enforces the same; this is a fast first pass).
export const ALLOWED_UPLOAD_TYPES = ["image/jpeg", "image/png", "image/webp"];
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024; // 10 MB

/**
 * POST a single-file multipart form to an uploads endpoint and unwrap the
 * ApiResponse envelope. Uses a raw fetch (not apiFetch) because the body is
 * FormData — the browser must set the multipart Content-Type/boundary, so we
 * only attach the auth headers. Shared by both upload variants.
 */
async function postImageUpload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      headers: authHeaders(),
      body: form
    });
  } catch {
    throw new ApiError("网络连接失败，请检查后端服务是否在线。", "NETWORK_ERROR", 0);
  }

  if (res.status === 401 && authStore.get()) authStore.clear();

  let payload: ApiResponse<T> | null = null;
  try {
    payload = (await res.json()) as ApiResponse<T>;
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

  return payload?.data as T;
}

/**
 * Upload an avatar image (数字人口播) → `asset_id`. Hits POST /uploads/images,
 * which persists an Asset row and returns its id.
 */
export function uploadImage(file: File): Promise<UploadImageResponse> {
  return postImageUpload<UploadImageResponse>("/api/v1/uploads/images", file);
}

/**
 * Upload a product image (电商带货 i2v) → `image_key`. Hits POST /uploads — NOT
 * /uploads/images — which stores the file under the tenant namespace and returns
 * `key`; the create-video request passes that as `image_key`.
 */
export async function uploadProductImage(file: File): Promise<{ image_key: string }> {
  const res = await postImageUpload<UploadResponse>("/api/v1/uploads", file);
  return { image_key: res.key };
}
