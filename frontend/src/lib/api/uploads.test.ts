import { afterEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL } from "@/lib/api/client";
import { MAX_UPLOAD_BYTES, uploadImage, uploadProductImage, validateImageFile } from "@/lib/api/uploads";
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

  it("超过体积上限 → 友好中文过大错误", () => {
    const big = new File([new Uint8Array(MAX_UPLOAD_BYTES + 1)], "big.png", { type: "image/png" });
    expect(validateImageFile(big)).toBe(copy.errors.uploadTooLarge);
  });
});
