import { describe, expect, it, vi } from "vitest";

import { ApiError } from "./client";
import { createBrandVoice, createBrandVoiceFromAudio, deleteBrandVoice, listBrandVoices, uploadAudio } from "./brand-voices";
import { listVoices } from "./voices";

// 集成测试：不 mock，真 apiFetch/multipartFetch → MSW。验证 mock 忠实(吸取教训)对齐 §8：
// 三段式 create、JSON + consent 校验(422)、BrandVoiceRead 无 sample_url、status 轮询、/voices source。
const jsonResponse = (data: unknown, status = 200) =>
  new Response(JSON.stringify({ data, error: null, request_id: "t" }), {
    status,
    headers: { "content-type": "application/json" }
  });

describe("brand-voices API ↔ MSW（mock 忠实，§8）", () => {
  // 承重①：create 三段式 —— 先 /uploads/audio(multipart file)，再 JSON /brand-voices
  // 带 consent_confirmed:true + source_audio_asset_id。删 consent 字段则此 toEqual 红。
  it("承重①：createBrandVoiceFromAudio 先 multipart /uploads/audio 再 JSON /brand-voices(带 consent + asset_id)", async () => {
    const audio = new Blob(["xxxx"], { type: "audio/webm" });
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ asset_id: "audio-1" }, 201))
      .mockResolvedValueOnce(jsonResponse({ id: "bv-1", name: "我的音", status: "processing", created_at: "" }, 201));
    try {
      await createBrandVoiceFromAudio({ name: "我的音", audio, consentConfirmed: true });
      expect(fetchSpy).toHaveBeenCalledTimes(2);

      // ① /uploads/audio：multipart，含 file
      const [u0, i0] = fetchSpy.mock.calls[0];
      expect(String(u0)).toContain("/api/v1/uploads/audio");
      expect((i0 as RequestInit).method).toBe("POST");
      const form = (i0 as RequestInit).body as FormData;
      expect(form).toBeInstanceOf(FormData);
      expect(form.get("file")).toBeInstanceOf(Blob);

      // ② /brand-voices：JSON，body 逐字含 consent_confirmed:true + source_audio_asset_id（来自①的 asset_id）
      const [u1, i1] = fetchSpy.mock.calls[1];
      expect(String(u1)).toContain("/api/v1/brand-voices");
      expect((i1 as RequestInit).method).toBe("POST");
      expect(((i1 as RequestInit).headers as Record<string, string>)["Content-Type"]).toBe("application/json");
      const body = JSON.parse((i1 as RequestInit).body as string);
      expect(body).toEqual({ name: "我的音", source_audio_asset_id: "audio-1", consent_confirmed: true });
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("uploadAudio：multipart → { asset_id }", async () => {
    const res = await uploadAudio(new Blob(["x"], { type: "audio/webm" }));
    expect(res.asset_id).toBeTruthy();
  });

  it("/uploads/audio 失败 → 三段式短路：不再发 JSON /brand-voices", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ data: null, error: { code: "AUDIO_UPLOAD_ERROR", message: "x" }, request_id: "t" }), {
          status: 500,
          headers: { "content-type": "application/json" }
        })
      );
    try {
      await expect(
        createBrandVoiceFromAudio({ name: "x", audio: new Blob(["x"], { type: "audio/webm" }), consentConfirmed: true })
      ).rejects.toBeInstanceOf(ApiError);
      expect(fetchSpy).toHaveBeenCalledTimes(1); // 仅 /uploads/audio，未短路到 /brand-voices
      expect(String(fetchSpy.mock.calls[0][0])).toContain("/api/v1/uploads/audio");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("createBrandVoice JSON：consent_confirmed=false → 422(后端 extra=forbid + consent 校验)", async () => {
    let caught: unknown;
    try {
      await createBrandVoice({ name: "x", source_audio_asset_id: "audio-1", consent_confirmed: false });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(422);
  });

  it("三段式整链：create → processing；BrandVoiceRead 无 sample_url/error_message", async () => {
    const created = await createBrandVoiceFromAudio({
      name: "整链音色",
      audio: new Blob(["x"], { type: "audio/webm" }),
      consentConfirmed: true
    });
    expect(created.id).toBeTruthy();
    expect(created.status).toBe("processing");
    expect(created).not.toHaveProperty("sample_url");
    expect(created).not.toHaveProperty("error_message");
  });

  it("列表 seed 含 ready 与 failed；status 轮询 processing→ready", async () => {
    const seeded = await listBrandVoices();
    expect(seeded.some((v) => v.status === "ready")).toBe(true);
    expect(seeded.some((v) => v.status === "failed")).toBe(true);

    const created = await createBrandVoiceFromAudio({
      name: "轮询音色",
      audio: new Blob(["x"], { type: "audio/webm" }),
      consentConfirmed: true
    });
    let resolved = created;
    for (let i = 0; i < 4 && resolved.status === "processing"; i++) {
      const items = await listBrandVoices();
      resolved = items.find((v) => v.id === created.id) ?? resolved;
    }
    expect(resolved.status).toBe("ready");
  });

  it("删除：DELETE 后列表不再含该 id", async () => {
    const created = await createBrandVoiceFromAudio({
      name: "待删音色",
      audio: new Blob(["x"], { type: "audio/webm" }),
      consentConfirmed: true
    });
    const res = await deleteBrandVoice(created.id);
    expect(res.deleted).toBe(true);
    const items = await listBrandVoices();
    expect(items.find((v) => v.id === created.id)).toBeUndefined();
  });

  it("/voices 含 source=brand_voice 克隆音色，供口播 picker 分组", async () => {
    const voices = await listVoices();
    expect(voices.some((v) => v.source === "brand_voice")).toBe(true);
    expect(voices.some((v) => v.source === "preset")).toBe(true);
  });
});
