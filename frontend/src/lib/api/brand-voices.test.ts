import { describe, expect, it, vi } from "vitest";

import { createBrandVoice, deleteBrandVoice, listBrandVoices } from "./brand-voices";
import { listVoices } from "./voices";

// 集成测试：不 mock，真 apiFetch/FormData → MSW。验证 mock 忠实(吸取教训)：
// CRUD、status(processing→ready 轮询)、/voices 含 ready 克隆(is_brand_voice)。

describe("brand-voices API ↔ MSW（mock 忠实）", () => {
  it("列表初始含 seed 的 ready 与 failed（带 sample_url / error_message）", async () => {
    const items = await listBrandVoices();
    const ready = items.find((v) => v.status === "ready");
    const failed = items.find((v) => v.status === "failed");
    expect(ready?.sample_url).toBeTruthy();
    expect(failed?.error_message).toBeTruthy();
  });

  it("创建：multipart(name+audio) → 返回 processing 记录", async () => {
    const audio = new Blob(["xxxx"], { type: "audio/webm" });
    const created = await createBrandVoice({ name: "测试音色", audio });
    expect(created.id).toBeTruthy();
    expect(created.name).toBeTruthy();
    expect(created.status).toBe("processing");
  });

  // FE 发送的 multipart 形状精确守护（jsdom 下 MSW 不稳定解析 multipart，故用 fetch spy 直查 body）。
  it("createBrandVoice 经 multipart 发送 name + audio 到 POST /brand-voices", async () => {
    const audio = new Blob(["xxxx"], { type: "audio/webm" });
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ data: { id: "bv-x", name: "形状音色", status: "processing", created_at: "" }, error: null, request_id: "t" }), {
        status: 200,
        headers: { "content-type": "application/json" }
      })
    );
    try {
      await createBrandVoice({ name: "形状音色", audio });
      const [url, init] = fetchSpy.mock.calls[0];
      expect(String(url)).toContain("/api/v1/brand-voices");
      expect((init as RequestInit).method).toBe("POST");
      const body = (init as RequestInit).body as FormData;
      expect(body).toBeInstanceOf(FormData);
      expect(body.get("name")).toBe("形状音色");
      expect(body.get("audio")).toBeInstanceOf(Blob);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("status 轮询：processing 连续轮询后翻 ready + 出 sample_url（非伪造，真转移）", async () => {
    const audio = new Blob(["xxxx"], { type: "audio/webm" });
    const created = await createBrandVoice({ name: "轮询音色", audio });
    expect(created.status).toBe("processing");

    // 轮询若干次直到该记录变 ready（mock 第 2 次轮询翻 ready）。
    let resolved = created;
    for (let i = 0; i < 4 && resolved.status === "processing"; i++) {
      const items = await listBrandVoices();
      resolved = items.find((v) => v.id === created.id) ?? resolved;
    }
    expect(resolved.status).toBe("ready");
    expect(resolved.sample_url).toBeTruthy();
  });

  it("删除：DELETE 后列表不再含该 id", async () => {
    const audio = new Blob(["xxxx"], { type: "audio/webm" });
    const created = await createBrandVoice({ name: "待删音色", audio });
    const res = await deleteBrandVoice(created.id);
    expect(res.deleted).toBe(true);
    const items = await listBrandVoices();
    expect(items.find((v) => v.id === created.id)).toBeUndefined();
  });

  it("/voices 含 ready 克隆音色（is_brand_voice），供口播 picker 分组", async () => {
    const voices = await listVoices();
    const clone = voices.find((v) => v.is_brand_voice);
    expect(clone).toBeTruthy();
    expect(clone?.is_brand_voice).toBe(true);
  });
});
