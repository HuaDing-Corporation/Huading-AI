import { http, HttpResponse } from "msw";

// Mirror client.ts's trailing-slash normalization so handler URLs always match
// what apiFetch requests (avoids a latent "mock silently bypassed" footgun).
const BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const ok = <T>(data: T) => HttpResponse.json({ data, error: null, request_id: "mock-req" });
const err = (status: number, code: string, message: string) =>
  HttpResponse.json({ data: null, error: { code, message, request_id: "mock-req" }, request_id: "mock-req" }, { status });

// in-memory store so list/detail/SSE stay consistent within a session
const videos = new Map<string, Record<string, unknown>>();

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
    const body = (await request.json()) as { topic: string };
    const id = `mock-${videos.size + 1}`;
    videos.set(id, { id, status: "queued", progress: 0, topic: body.topic, created_at: new Date(0).toISOString(), script: body.topic, voice_id: "v-zhixing", aspect_ratio: "9:16", subtitle_enabled: true });
    return HttpResponse.json({ data: { id, status: "queued" }, error: null, request_id: "mock-req" }, { status: 202 });
  }),
  http.get(`${BASE}/api/v1/videos`, () =>
    ok({ items: [...videos.values()], total: videos.size })
  ),
  http.get(`${BASE}/api/v1/videos/:id`, ({ params }) => {
    const v = videos.get(String(params.id));
    return v ? ok(v) : err(404, "not_found", "视频不存在");
  }),
  http.get(`${BASE}/api/v1/videos/:id/events`, ({ params, request }) =>
    sseStream(String(params.id), new URL(request.url).searchParams.get("fail") === "1")
  )
];
