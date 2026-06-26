import { http, HttpResponse } from "msw";

// Mirror client.ts's trailing-slash normalization so handler URLs always match
// what apiFetch requests (avoids a latent "mock silently bypassed" footgun).
const BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const ok = <T>(data: T) => HttpResponse.json({ data, error: null, request_id: "mock-req" });
const err = (status: number, code: string, message: string) =>
  HttpResponse.json({ data: null, error: { code, message, request_id: "mock-req" }, request_id: "mock-req" }, { status });

// in-memory store so list/detail/SSE stay consistent within a session
const videos = new Map<string, Record<string, unknown>>();
// 文案草稿内存 store（newest first），供 POST/GET /copy/drafts 一致回放
const copyDrafts: Record<string, unknown>[] = [];
// 单调递增 id 计数器：删后重建不复用 id（videos+covers 共享 Map 故共用一个），避免碰撞/重复(HIST-UI-0001 RV)
let videoSeq = 0;
let draftSeq = 0;

function sseStream(id: string, fail = false): Response {
  const enc = new TextEncoder();
  const frames = fail
    ? [
        { status: "running", progress: 10, step: "tts" },
        { status: "failed", error_code: "avatar_provider_failed", error_message: "形象生成失败（mock）" }
      ]
    : [
        { status: "running", progress: 10, step: "script" },
        { status: "running", progress: 40, step: "avatar" },
        { status: "running", progress: 95, step: "compose" },
        {
          status: "done",
          progress: 100,
          playback_url: "https://mock.local/v.mp4",
          download_url: "https://mock.local/v.mp4?dl=1",
          thumbnail_url: "https://mock.local/t.jpg"
        }
      ];
  const stream = new ReadableStream({
    start(controller) {
      let i = 0;
      const push = () => {
        if (i >= frames.length) return controller.close();
        const f = frames[i++];
        controller.enqueue(enc.encode(`data: ${JSON.stringify(f)}\n\n`));
        const term = f.status === "done" || f.status === "failed";
        if (term) {
          // reflect terminal into the detail store so done-reconcile works
          videos.set(id, { ...(videos.get(id) ?? {}), ...f, id });
          return controller.close();
        }
        videos.set(id, { ...(videos.get(id) ?? {}), ...f, id });
        setTimeout(push, 120);
      };
      push();
    }
  });
  return new Response(stream, { headers: { "Content-Type": "text/event-stream" } });
}

export const handlers = [
  // Auth = M2 shapes (unchanged). Mocked so the (app) client auth-gate can be
  // passed during the MSW parallel period without a real backend.
  http.post(`${BASE}/api/v1/auth/login`, () =>
    ok({ access_token: "mock-token", token_type: "bearer", tenant_id: "ten-mock", user_id: "u-mock", role: "admin" })
  ),
  http.get(`${BASE}/api/v1/auth/me`, () =>
    ok({
      tenant: { id: "ten-mock", slug: "huading", name: "华鼎（mock）" },
      user: { id: "u-mock", tenant_id: "ten-mock", email: "qa@huading.test", full_name: "QA 测试", role: "admin" },
      permissions: ["video:create", "video:read"]
    })
  ),
  http.get(`${BASE}/api/v1/quota`, () => ok({ total: 1000, used: 120, reserved: 36, remaining: 844 })),
  http.get(`${BASE}/api/v1/voices`, () =>
    ok({
      items: [
        { id: "v-zhixing", provider: "edge_tts", voice_code: "zh-CN-XiaoxiaoNeural", display_name: "知性女声", gender: "female", language: "zh-CN", sample_url: null }
      ],
      total: 1
    })
  ),
  http.get(`${BASE}/api/v1/avatars/presets`, () =>
    ok({ items: [{ asset_id: "preset-1", display_name: "默认主播", thumbnail_url: "https://mock.local/p1.jpg" }], total: 1 })
  ),
  http.post(`${BASE}/api/v1/scripts/generate`, async ({ request }) => {
    const body = (await request.json()) as { topic: string };
    return ok({ script: `【${body.topic}】大家好，今天用一分钟带你了解……（mock 文案，可编辑）` });
  }),
  http.post(`${BASE}/api/v1/uploads/images`, () =>
    HttpResponse.json(
      { data: { asset_id: "upload-1", type: "avatar_image", status: "ready", thumbnail_url: "https://mock.local/u1.jpg" }, error: null, request_id: "mock-req" },
      { status: 201 }
    )
  ),
  http.post(`${BASE}/api/v1/videos`, async ({ request }) => {
    const body = (await request.json()) as { topic: string; video_mode?: string; purpose?: string };
    const id = `mock-${++videoSeq}`;
    // 记 mode + kind(AI 封面 purpose=cover → kind=cover)，让 GET /videos 的 mode/kind 筛忠实回放
    videos.set(id, { id, status: "queued", progress: 0, topic: body.topic, mode: body.video_mode ?? "avatar_talk", kind: body.purpose === "cover" ? "cover" : null, created_at: new Date(0).toISOString(), script: body.topic, voice_id: "v-zhixing", aspect_ratio: "9:16", subtitle_enabled: true });
    return HttpResponse.json({ data: { id, status: "queued" }, error: null, request_id: "mock-req" }, { status: 202 });
  }),
  http.get(`${BASE}/api/v1/videos`, ({ request }) => {
    const sp = new URL(request.url).searchParams;
    const mode = sp.get("mode");
    const kind = sp.get("kind");
    // 忠实后端 mode+kind 真过滤(kind=cover → 仅封面 photo task)；不伪造记录(HIST-UI-0001)
    const items = [...videos.values()].filter((v) => (!mode || v.mode === mode) && (!kind || v.kind === kind));
    return ok({ items, total: items.length });
  }),
  http.get(`${BASE}/api/v1/videos/:id`, ({ params }) => {
    const v = videos.get(String(params.id));
    return v ? ok(v) : err(404, "not_found", "视频不存在");
  }),
  http.get(`${BASE}/api/v1/videos/:id/events`, ({ params, request }) =>
    sseStream(String(params.id), new URL(request.url).searchParams.get("fail") === "1")
  ),
  // ── 历史删除 / 清空 (HIST-UI-0001) — 硬删，真从 store 删 ──
  // 清空(DELETE /videos?mode=) 必须先于 /videos/:id 注册，避免无 id 时被 :id 误捕。
  http.delete(`${BASE}/api/v1/videos`, ({ request }) => {
    const mode = new URL(request.url).searchParams.get("mode");
    if (!mode) return err(422, "mode_required", "mode 必填");
    let count = 0;
    for (const [id, v] of [...videos.entries()]) {
      if (v.mode === mode) {
        videos.delete(id);
        count++;
      }
    }
    return ok({ deleted_count: count });
  }),
  http.delete(`${BASE}/api/v1/videos/:id`, ({ params }) => {
    const id = String(params.id);
    if (!videos.has(id)) return err(404, "not_found", "记录不存在");
    videos.delete(id);
    return ok({ deleted: true });
  }),

  // ── 文案仿写 + 标题/话题生成 (COPY-UI-0001) — 同步 REST mock ──
  http.post(`${BASE}/api/v1/copy/rewrite`, async ({ request }) => {
    const body = (await request.json()) as { source_text: string; mode: string; n?: number };
    const base = (body.source_text ?? "").trim();
    if (body.mode === "auto") {
      const n = Math.min(5, Math.max(1, body.n ?? 3));
      return ok({
        results: Array.from({ length: n }, (_, i) => ({ text: `【版本 ${i + 1}】${base}（mock 改写，可编辑）` }))
      });
    }
    return ok({ results: [{ text: `${base}（mock 改写，可编辑）` }] });
  }),
  http.post(`${BASE}/api/v1/copy/titles`, async ({ request }) => {
    const body = (await request.json()) as { n?: number };
    const n = body.n ?? 5;
    return ok({ titles: Array.from({ length: n }, (_, i) => `mock 标题候选 ${i + 1}`) });
  }),
  http.post(`${BASE}/api/v1/copy/topics`, async ({ request }) => {
    const body = (await request.json()) as { n?: number };
    const n = body.n ?? 5;
    return ok({ topics: Array.from({ length: n }, (_, i) => `#mock话题${i + 1}`) });
  }),
  http.post(`${BASE}/api/v1/copy/drafts`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const draft = { id: `draft-${++draftSeq}`, created_at: new Date(0).toISOString(), ...body };
    copyDrafts.unshift(draft);
    return HttpResponse.json({ data: draft, error: null, request_id: "mock-req" }, { status: 201 });
  }),
  http.get(`${BASE}/api/v1/copy/drafts`, () => ok({ items: copyDrafts, total: copyDrafts.length })),
  // 文案删除 / 清空 (HIST-UI-0001) — 软删(mock 从可见列表移除)
  http.delete(`${BASE}/api/v1/copy/drafts`, () => {
    const count = copyDrafts.length;
    copyDrafts.length = 0;
    return ok({ deleted_count: count });
  }),
  http.delete(`${BASE}/api/v1/copy/drafts/:id`, ({ params }) => {
    const i = copyDrafts.findIndex((d) => d.id === String(params.id));
    if (i < 0) return err(404, "not_found", "草稿不存在");
    copyDrafts.splice(i, 1);
    return ok({ deleted: true });
  }),

  // ── 口播生产力增强 (ORAL-PROD-UI-0001) — 字幕模板 + 封面截帧 mock ──
  http.get(`${BASE}/api/v1/oral/subtitle-templates`, () =>
    ok({
      templates: [
        { id: "classic", name: "经典白", font_family: "Noto Sans SC", font_size: 48, color: "#FFFFFF", stroke_color: "#000000", stroke_width: 2, background: null, position: "bottom" },
        { id: "bold_yellow", name: "醒目黄", font_family: "Noto Sans SC", font_size: 56, color: "#FFE600", stroke_color: "#000000", stroke_width: 3, background: null, position: "bottom" },
        { id: "boxed", name: "底条黑", font_family: "Noto Sans SC", font_size: 46, color: "#FFFFFF", stroke_color: null, stroke_width: 0, background: "#000000B3", position: "bottom" },
        { id: "minimal", name: "极简灰", font_family: "Noto Sans SC", font_size: 40, color: "#EAEAEA", stroke_color: null, stroke_width: 0, background: null, position: "bottom" },
        { id: "top_news", name: "顶部条", font_family: "Noto Sans SC", font_size: 44, color: "#FFFFFF", stroke_color: "#000000", stroke_width: 2, background: "#0A0A0AB3", position: "top" }
      ]
    })
  ),
  http.get(`${BASE}/api/v1/covers/frame-candidates`, ({ request }) => {
    const n = Math.min(10, Math.max(1, Number(new URL(request.url).searchParams.get("count")) || 5));
    return ok({
      frames: Array.from({ length: n }, (_, i) => ({
        timestamp_sec: Number((i * 1.5).toFixed(1)),
        preview_url: `https://mock.local/frame-${i}.jpg`
      }))
    });
  }),
  http.post(`${BASE}/api/v1/covers/from-frame`, () => {
    // seam §1：截帧封面建成 photo VideoTask(kind=cover, done) → 忠实进图片历史 + kind 筛筛出。
    // (与 FIX1 不同：那时后端不支持、mock 伪造掩盖断链；现 HIST 后端真建 photo task，故塞真记录。)
    const id = `cover-${++videoSeq}`;
    videos.set(id, {
      id, status: "done", progress: 100, topic: "封面", mode: "photo", kind: "cover",
      created_at: new Date(0).toISOString(), playback_url: "https://mock.local/cover.png",
      download_url: "https://mock.local/cover.png?dl=1", thumbnail_url: "https://mock.local/cover.png"
    });
    return ok({ cover: { id, image_url: "https://mock.local/cover.png", width: 1280, height: 720 } });
  })
];
