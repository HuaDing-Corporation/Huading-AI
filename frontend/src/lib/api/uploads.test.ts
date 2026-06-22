import { afterEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL } from "@/lib/api/client";
import { uploadImage, uploadProductImage } from "@/lib/api/uploads";

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
