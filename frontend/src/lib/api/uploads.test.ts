import { afterEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL } from "@/lib/api/client";
import { uploadAvatarVideo, uploadImage, uploadProductImage, uploadReverseVideo, uploadVideoGenReference, validateImageFile } from "@/lib/api/uploads";
import { validateAvatarVideoFile } from "@/lib/media/avatar-video";
import { validateReverseVideoFile } from "@/lib/media/reverse-video";
import { validateReferenceVideoFile } from "@/lib/media/reference-video";
import { copy } from "@/lib/copy";

function mockFetchOnce(data: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 201,
    json: async () => ({ data, error: null, request_id: null })
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("uploads — i2v product image vs avatar image (correct endpoints)", () => {
  it("uploadProductImage posts to POST /uploads and maps key → image_key (i2v)", async () => {
    const fetchMock = mockFetchOnce({ key: "uploads/abc123.png", content_type: "image/png", size: 10 });
    const file = new File(["x"], "p.png", { type: "image/png" });

    const result = await uploadProductImage(file);

    // Maps the backend `key` to the create-video `image_key`.
    expect(result).toEqual({ image_key: "uploads/abc123.png" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_BASE_URL}/api/v1/uploads`);
    // Must NOT hit the avatar endpoint (that returns asset_id, not image_key).
    expect(url).not.toContain("/uploads/images");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("uploadImage posts to POST /uploads/images and returns asset_id (avatar, unchanged)", async () => {
    const fetchMock = mockFetchOnce({ asset_id: "asset-1", type: "avatar_image", status: "ready" });
    const file = new File(["x"], "a.png", { type: "image/png" });

    const result = await uploadImage(file);

    expect(result.asset_id).toBe("asset-1");
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/api/v1/uploads/images`);
  });
});

describe("video transports do not inherit the image cap", () => {
  it.each([
    { upload: uploadAvatarVideo, validate: validateAvatarVideoFile, bytes: 209715200, path: "/api/v1/uploads/videos" },
    { upload: uploadReverseVideo, validate: validateReverseVideoFile, bytes: 209715200, path: "/api/v1/uploads/videos?purpose=reverse_prompt" },
    { upload: uploadVideoGenReference, validate: validateReferenceVideoFile, bytes: 104857600, path: "/api/v1/uploads/videos?purpose=video_gen_reference" }
  ])("preserves $bytes bytes for $path", async ({ upload, validate, bytes, path }) => {
    // Metadata-sized File: verifies routing/guard separation without allocating
    // repeated 200MiB buffers. Not evidence of actual 200MiB server acceptance.
    const file = new File(["video"], "source.mp4", { type: "video/mp4" });
    Object.defineProperty(file, "size", { value: bytes });
    expect(validate(file)).toBeNull();
    const oversize = new File(["video"], "oversize.mp4", { type: "video/mp4" });
    Object.defineProperty(oversize, "size", { value: bytes + 1 });
    expect(validate(oversize)).not.toBeNull();
    const fetchMock = mockFetchOnce({ asset_id: "video-1", type: "video", status: "ready" });
    await expect(upload(file)).resolves.toMatchObject({ asset_id: "video-1" });
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}${path}`);
    expect(fetchMock.mock.calls[0][1].body.get("file")).toBe(file);
  });
});

// REVERSE-PROMPT-UI-0001 客户端校验红线：按真实 MIME（file.type）判定，**不信文件名后缀**；类型/体积超限
// 返回友好中文，合规返回 null。提示词反推上传入口据此拦截，改判据此断言应红。
describe("validateImageFile — 反推上传客户端校验（不信文件名，按 MIME）", () => {
  it("合法 JPG/PNG/WebP（在限额内）→ null（放行）", () => {
    for (const type of ["image/jpeg", "image/png", "image/webp"]) {
      expect(validateImageFile(new File(["x"], `f`, { type }))).toBeNull();
    }
  });

  it("非白名单类型（gif）→ 友好中文类型错误", () => {
    expect(validateImageFile(new File(["x"], "a.gif", { type: "image/gif" }))).toBe(copy.errors.uploadType);
  });

  it("承重·不信文件名：伪装成 .png 但真实 MIME 非图片 → 仍按 MIME 判类型错误", () => {
    // 攻击面：把 evil.exe 改名 a.png。客户端必须按 file.type 而非扩展名判定。
    const disguised = new File(["x"], "a.png", { type: "application/octet-stream" });
    expect(validateImageFile(disguised)).toBe(copy.errors.uploadType);
  });

  it.each([10485761, 20971520, 31457280])("接受 %i bytes 图片（含旧 10MiB 以上及新上界）", (bytes) => {
    const image = new File([new Uint8Array(bytes)], "image.png", { type: "image/png" });
    expect(validateImageFile(image)).toBeNull();
  });

  it("31457281 bytes → 友好中文过大错误", () => {
    const big = new File([new Uint8Array(31457281)], "big.png", { type: "image/png" });
    expect(validateImageFile(big)).toBe(copy.errors.uploadTooLarge);
    expect(validateImageFile(big)).toContain("30MB");
  });
});
