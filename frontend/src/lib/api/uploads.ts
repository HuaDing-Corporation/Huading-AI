import { multipartFetch } from "@/lib/api/client";
import { copy } from "@/lib/copy";
import type { AvatarVideoUploadResponse, UploadImageResponse, UploadResponse } from "@/lib/api/types";

// Client-side guards (the backend enforces the same; this is a fast first pass).
export const ALLOWED_UPLOAD_TYPES = ["image/jpeg", "image/png", "image/webp"];
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024; // 10 MB

/**
 * 图片上传客户端校验（快速第一道；后端仍会二次把关）。**按真实 MIME（file.type）判定，不信文件名后缀**——
 * 把 evil.exe 改名 a.png 仍会被 type 挡下。返回友好中文错误串，合规返回 null。供提示词反推等上传入口复用。
 */
export function validateImageFile(file: File): string | null {
  if (!ALLOWED_UPLOAD_TYPES.includes(file.type)) return copy.errors.uploadType;
  if (file.size > MAX_UPLOAD_BYTES) return copy.errors.uploadTooLarge;
  return null;
}

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

/**
 * Upload a 本人出镜视频 (数字人口播·视频源) → `asset_id`. Hits POST /uploads/videos (镜像 /uploads/images
 * ·/uploads/audio 的 multipart 上传)，返回的 asset_id 作 create-video 的 avatar_video_asset_id。
 * AVATAR-VIDEO-SOURCE-UI-0001；端点/形状以 BE 包为准，mock 先行。
 */
export function uploadAvatarVideo(file: File): Promise<AvatarVideoUploadResponse> {
  return postImageUpload<AvatarVideoUploadResponse>("/api/v1/uploads/videos", file);
}

/**
 * Upload a 视频反推 source video (提示词反推·视频) → `asset_id`. 复用 POST /uploads/videos，带 purpose=reverse_prompt
 * 让后端按用途归类（VIDEO-REVERSE-PROMPT-UI-0001；端点/形状以 BE-0001 为准，mock 先行）。
 */
export function uploadReverseVideo(file: File): Promise<AvatarVideoUploadResponse> {
  return postImageUpload<AvatarVideoUploadResponse>("/api/v1/uploads/videos?purpose=reverse_prompt", file);
}

/**
 * Upload a 视频生成·参考视频 → `asset_id`. 复用 POST /uploads/videos，带 purpose=video_gen_reference（第三个
 * purpose——现有 avatar_source 3–10s / reverse_prompt 1–60s 的时长约束都不适用，冻结文档·需求6 实现要点④）。
 * VIDEO-GEN-V2V-UI-0001；端点/purpose 命名以 BE 包为准，mock 先行，BE 合并后真联调对齐。
 */
export function uploadVideoGenReference(file: File): Promise<AvatarVideoUploadResponse> {
  return postImageUpload<AvatarVideoUploadResponse>("/api/v1/uploads/videos?purpose=video_gen_reference", file);
}
