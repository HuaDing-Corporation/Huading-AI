import { afterEach, describe, expect, it, vi } from "vitest";

import { apiFetch, ApiError, API_BASE_URL } from "./client";
import { uploadAudio } from "./brand-voices";
import { errorText } from "./error-text";
import { uploadAvatarVideo, uploadImage, uploadProductImage, uploadReverseVideo, uploadVideoGenReference } from "./uploads";

const media = [
  { upload: uploadProductImage, path: "/api/v1/uploads" },
  { upload: uploadImage, path: "/api/v1/uploads/images" },
  { upload: uploadAvatarVideo, path: "/api/v1/uploads/videos" },
  { upload: uploadReverseVideo, path: "/api/v1/uploads/videos?purpose=reverse_prompt" },
  { upload: uploadVideoGenReference, path: "/api/v1/uploads/videos?purpose=video_gen_reference" }
];
const smallFile = () => new File(["x"], "small", { type: "image/png" });

function respond(status: number, body: string) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe.each(media)("media upload errors: $path", ({ upload, path }) => {
  // Break caught: missing domain mapping, lost ApiError diagnostics, or mapping
  // only one image endpoint while missing video purpose/query variants.
  it.each(["UPLOAD_TOO_LARGE", "REQUEST_BODY_TOO_LARGE"])("JSON 413 %s keeps diagnostics and explains the request limit", async (code) => {
    const detail = { limit_bytes: 10485760, received_bytes: 10485960, layer: "body" };
    const outcome = { operation: "upload", state: "rejected" };
    const fetchMock = respond(413, JSON.stringify({
      data: null, error: { code, message: "Request too large", detail, outcome }, request_id: "upload-413"
    }));
    const error = await upload(smallFile()).catch((err: unknown) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code, status: 413, detail, outcome });
    expect((error as ApiError).message).toMatch(/上传请求.*大小限制/);
    expect(errorText(error)).toMatch(/服务或网关/);
    expect(errorText(error)).toMatch(/即使.*仍可能/);
    expect(errorText(error)).not.toMatch(/30MB|100MB|200MB|400MB|自动压缩/);
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}${path}`);
  });

  it.each(["Request Entity Too Large", "<html><h1>413 Request Entity Too Large</h1></html>"])("non-JSON gateway 413: %s", async (body) => {
    respond(413, body);
    const error = await upload(smallFile()).catch((err: unknown) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: "UPLOAD_ERROR", status: 413 });
    expect(errorText(error)).toMatch(/上传请求.*大小限制/);
    expect(errorText(error)).toMatch(/服务或网关/);
  });

  it.each([
    { status: 415, code: "UNSUPPORTED_MEDIA_TYPE", message: "Unsupported video type" },
    { status: 422, code: "REVERSE_PROMPT_VIDEO_CODEC_INVALID", message: "Video must use H.264" }
  ])("does not relabel ordinary $status as too large", async ({ status, code, message }) => {
    const detail = { field: "file" };
    respond(status, JSON.stringify({ data: null, error: { code, message, detail }, request_id: "invalid-media" }));
    const error = await upload(smallFile()).catch((err: unknown) => err);
    expect(error).toMatchObject({ status, code, message, detail });
    expect(errorText(error)).toBe(message);
  });

  it("keeps NETWORK_ERROR rather than inventing a size failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    const error = await upload(smallFile()).catch((err: unknown) => err);
    expect(error).toMatchObject({ code: "NETWORK_ERROR", status: 0 });
    expect(errorText(error)).toContain("网络连接失败");
  });
});

describe("upload-domain isolation", () => {
  it.each([
    { body: "plain gateway 413", code: "AUDIO_UPLOAD_ERROR", message: "音频上传失败（413）" },
    { body: JSON.stringify({ data: null, error: { code: "UPLOAD_TOO_LARGE", message: "Audio too large" }, request_id: "audio" }), code: "UPLOAD_TOO_LARGE", message: "Audio too large" }
  ])("keeps audio 413 unchanged: $code", async ({ body, code, message }) => {
    respond(413, body);
    await expect(uploadAudio(new Blob(["audio"]))).rejects.toMatchObject({ code, status: 413, message });
  });

  it("does not remap non-upload API 413", async () => {
    respond(413, "plain 413");
    await expect(apiFetch("/api/v1/videos")).rejects.toMatchObject({ code: "HTTP_ERROR", status: 413, message: "请求失败（413）" });
  });
});
