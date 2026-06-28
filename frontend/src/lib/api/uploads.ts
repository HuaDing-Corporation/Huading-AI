import { multipartFetch } from "@/lib/api/client";
import type { UploadImageResponse, UploadResponse } from "@/lib/api/types";

// Client-side guards (the backend enforces the same; this is a fast first pass).
export const ALLOWED_UPLOAD_TYPES = ["image/jpeg", "image/png", "image/webp"];
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024; // 10 MB

/** 单文件 multipart 上传 → 解包封套。复用 client.multipartFetch（鉴权/401/封套单一实现）。 */
function postImageUpload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  return multipartFetch<T>(path, form, { defaultErrorMessage: "上传失败", defaultErrorCode: "UPLOAD_ERROR" });
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
