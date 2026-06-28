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

// ── 品牌音色 / 声音克隆 (BRAND-VOICE-UI-0001，FIX1 对齐后端 §8) mock store ──
// 忠实真契约：BrandVoiceRead 仅 {id,name,status,created_at}(无 sample_url/error_message)；CRUD +
// status(processing→ready 轮询模拟) + /voices 项带 source。非伪造。
interface MockBrandVoice {
  id: string;
  name: string;
  status: "processing" | "ready" | "failed";
  created_at: string;
  _polls: number; // GET 轮询计数：processing 第 2 次轮询后翻 ready，模拟异步克隆完成
}
const brandVoices = new Map<string, MockBrandVoice>([
  ["bv-ready-1", { id: "bv-ready-1", name: "我的主播音", status: "ready", created_at: new Date(0).toISOString(), _polls: 99 }],
  ["bv-failed-1", { id: "bv-failed-1", name: "失败样例", status: "failed", created_at: new Date(0).toISOString(), _polls: 99 }]
]);
let brandVoiceSeq = 0;
let audioAssetSeq = 0;

// ── 深度合成标识设置 (LABEL-UI-0001) mock store ──
// 忠实契约：enabled 只读恒真(合规不可关)；PUT 校验 text 非空 ≤20(否则 422)；非伪造。
const labelSettings = { position: "br", text: "AI 生成", enabled: true };

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
  // 预 seeded 终态(cutout/cover：POST 时已 done + 真实产物 URL)→ SSE done 帧不得用通用
  // v.mp4 覆盖其 URL，否则轮询 reconcile 会拿到错图(mock 忠实，吸取教训)。
  const seeded = videos.get(id);
  const preseeded = !fail && seeded?.status === "done" && Boolean(seeded.playback_url);
  const stream = new ReadableStream({
    start(controller) {
      let i = 0;
      const push = () => {
        if (i >= frames.length) return controller.close();
        const f = frames[i++];
        controller.enqueue(enc.encode(`data: ${JSON.stringify(f)}\n\n`));
        const term = f.status === "done" || f.status === "failed";
        if (term && preseeded) {
          // 仅确认 done，保留 POST 塞入的真实产物 URL(cutout-white/transparent.png)
          videos.set(id, { ...(videos.get(id) ?? {}), status: "done", progress: 100, id });
          return controller.close();
        }
        videos.set(id, { ...(videos.get(id) ?? {}), ...f, id });
        if (term) return controller.close();
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
  http.get(`${BASE}/api/v1/voices`, () => {
    // 系统音色(source:preset) + ready 克隆音色(source:brand_voice，对应 brand-voices ready 记录)，供 picker 分组(§8)。
    const clones = [...brandVoices.values()]
      .filter((v) => v.status === "ready")
      .map((v) => ({ id: v.id, provider: "clone", voice_code: v.id, display_name: v.name, gender: "neutral", language: "zh-CN", sample_url: null, source: "brand_voice" }));
    const items = [
      { id: "v-zhixing", provider: "edge_tts", voice_code: "zh-CN-XiaoxiaoNeural", display_name: "知性女声", gender: "female", language: "zh-CN", sample_url: null, source: "preset" },
      ...clones
    ];
    return ok({ items, total: items.length });
  }),
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
  }),

  // ── 电商图扩展 Phase1 (ECOM-IMG-UI-0001) — 白底图/抠图(单张 + 批量) mock ──
  // 忠实后端：塞真 photo VideoTask(kind=ecom_cutout, done)进 videos store，使现有
  // GET /videos/:id 轮询拿到 done + 图；批量 N clamp 1..20。非伪造掩盖(吸取历史教训)。
  http.post(`${BASE}/api/v1/ecom-images/cutout`, async ({ request }) => {
    const body = (await request.json()) as { source_asset_id: string; background: string };
    const id = `mock-${++videoSeq}`;
    const url =
      body.background === "transparent"
        ? "https://mock.local/cutout-transparent.png"
        : "https://mock.local/cutout-white.png";
    videos.set(id, {
      id, status: "done", progress: 100, topic: body.background === "transparent" ? "透明底商品图" : "白底商品图",
      mode: "photo", kind: "ecom_cutout", created_at: new Date(0).toISOString(),
      playback_url: url, download_url: `${url}?dl=1`, thumbnail_url: url
    });
    return ok({ task_id: id, status: "queued" });
  }),
  http.post(`${BASE}/api/v1/ecom-images/cutout/batch`, async ({ request }) => {
    const body = (await request.json()) as { items: { source_asset_id: string; background: string }[] };
    const items = (body.items ?? []).slice(0, 20); // N clamp 上界 20
    const batchId = `batch-${++videoSeq}`;
    const tasks = items.map((it) => {
      const id = `mock-${++videoSeq}`;
      const url =
        it.background === "transparent"
          ? "https://mock.local/cutout-transparent.png"
          : "https://mock.local/cutout-white.png";
      videos.set(id, {
        id, status: "done", progress: 100, topic: "批量抠图",
        mode: "photo", kind: "ecom_cutout", created_at: new Date(0).toISOString(),
        playback_url: url, download_url: `${url}?dl=1`, thumbnail_url: url
      });
      return { task_id: id, source_asset_id: it.source_asset_id, status: "queued" };
    });
    return ok({ batch_id: batchId, tasks });
  }),

  // ── 电商图扩展 Phase2 (ECOM-MODEL-UI-0001) — AI 模特(单张 + 批量) mock ──
  // 忠实后端：model-styles 返真列表；单张/批量塞真 photo VideoTask(kind=ecom_model, done)进
  // videos store，使现有 GET /videos/:id 轮询拿到 done + 模特图；批量 N clamp 1..20。非伪造(吸取教训)。
  // 忠实后端 EcomModelStyle(仅 id+name)与真实预设列表(studio_white/lifestyle/street)，不伪造 thumbnail。
  http.get(`${BASE}/api/v1/ecom-images/model-styles`, () =>
    ok({
      styles: [
        { id: "studio_white", name: "Studio white" },
        { id: "lifestyle", name: "Lifestyle" },
        { id: "street", name: "Street style" }
      ]
    })
  ),
  http.post(`${BASE}/api/v1/ecom-images/model`, async ({ request }) => {
    const body = (await request.json()) as { source_asset_id: string; gender: string; style_id: string };
    const id = `mock-${++videoSeq}`;
    const url = `https://mock.local/model-${body.style_id || "studio"}.png`;
    videos.set(id, {
      id, status: "done", progress: 100, topic: "AI 模特图",
      mode: "photo", kind: "ecom_model", created_at: new Date(0).toISOString(),
      playback_url: url, download_url: `${url}?dl=1`, thumbnail_url: url
    });
    return ok({ task_id: id, status: "queued" });
  }),
  http.post(`${BASE}/api/v1/ecom-images/model/batch`, async ({ request }) => {
    const body = (await request.json()) as { items: { source_asset_id: string; style_id: string }[] };
    const items = (body.items ?? []).slice(0, 20); // N clamp 上界 20
    const batchId = `batch-${++videoSeq}`;
    const tasks = items.map((it) => {
      const id = `mock-${++videoSeq}`;
      const url = `https://mock.local/model-${it.style_id || "studio"}.png`;
      videos.set(id, {
        id, status: "done", progress: 100, topic: "批量 AI 模特",
        mode: "photo", kind: "ecom_model", created_at: new Date(0).toISOString(),
        playback_url: url, download_url: `${url}?dl=1`, thumbnail_url: url
      });
      return { task_id: id, source_asset_id: it.source_asset_id, status: "queued" };
    });
    return ok({ batch_id: batchId, tasks });
  }),

  // ── 电商图扩展 Phase3 (ECOM-POSTER-UI-0001) — 营销海报(单张 + 批量) mock ──
  // 忠实后端：poster-templates 返列表(仅 id+name)；单张/批量塞真 photo VideoTask(kind=ecom_poster, done)
  // 进 videos store，GET /videos/:id 轮询拿到 done + 海报图；批量 N clamp 1..20。非伪造(吸取教训)。
  // 模板 id 逐字对齐后端真实预设(promo_bold/minimal/festival，ecom_images.py)，name 用中文展示名；
  // 真列表运行时来自 API，此处仅 dev/test 回放，不得用错 id 掩盖契约偏移(否则真后端 422)。
  http.get(`${BASE}/api/v1/ecom-images/poster-templates`, () =>
    ok({
      templates: [
        { id: "promo_bold", name: "大促爆款" },
        { id: "minimal", name: "简约高级" },
        { id: "festival", name: "节日喜庆" }
      ]
    })
  ),
  http.post(`${BASE}/api/v1/ecom-images/poster`, async ({ request }) => {
    const body = (await request.json()) as { source_asset_id: string; template_id: string };
    const id = `mock-${++videoSeq}`;
    const url = `https://mock.local/poster-${body.template_id || "promo_bold"}.png`;
    videos.set(id, {
      id, status: "done", progress: 100, topic: "营销海报",
      mode: "photo", kind: "ecom_poster", created_at: new Date(0).toISOString(),
      playback_url: url, download_url: `${url}?dl=1`, thumbnail_url: url
    });
    return ok({ task_id: id, status: "queued" });
  }),
  http.post(`${BASE}/api/v1/ecom-images/poster/batch`, async ({ request }) => {
    const body = (await request.json()) as { items: { source_asset_id: string; template_id: string }[] };
    const items = (body.items ?? []).slice(0, 20); // N clamp 上界 20
    const batchId = `batch-${++videoSeq}`;
    const tasks = items.map((it) => {
      const id = `mock-${++videoSeq}`;
      const url = `https://mock.local/poster-${it.template_id || "promo_bold"}.png`;
      videos.set(id, {
        id, status: "done", progress: 100, topic: "批量营销海报",
        mode: "photo", kind: "ecom_poster", created_at: new Date(0).toISOString(),
        playback_url: url, download_url: `${url}?dl=1`, thumbnail_url: url
      });
      return { task_id: id, source_asset_id: it.source_asset_id, status: "queued" };
    });
    return ok({ batch_id: batchId, tasks });
  }),

  // ── 品牌音色 / 声音克隆 (BRAND-VOICE-UI-0001，FIX1 §8) — 三段式 CRUD mock ──
  // 忠实真契约：① /uploads/audio(multipart file)→{asset_id}；② /brand-voices = JSON(extra=forbid)
  // 校验 consent_confirmed:true + source_audio_asset_id(缺/false→422)→{id,name,status,created_at}；
  // GET 列表 processing 轮询 2 次后翻 ready；DELETE→{deleted}。BrandVoiceRead 不含 sample_url/error_message。
  http.post(`${BASE}/api/v1/uploads/audio`, () => ok({ asset_id: `audio-asset-${++audioAssetSeq}` })),
  http.get(`${BASE}/api/v1/brand-voices`, () => {
    const items = [...brandVoices.values()].map((v) => {
      if (v.status === "processing") {
        v._polls += 1;
        if (v._polls >= 2) v.status = "ready";
      }
      return { id: v.id, name: v.name, status: v.status, created_at: v.created_at };
    });
    return ok({ items, total: items.length });
  }),
  http.post(`${BASE}/api/v1/brand-voices`, async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as {
      name?: string;
      source_audio_asset_id?: string;
      consent_confirmed?: boolean;
    };
    // 真后端 extra=forbid + consent 校验：缺 source_audio_asset_id 或 consent_confirmed!==true → 422。
    if (!body.source_audio_asset_id || body.consent_confirmed !== true) {
      // 错误码逐字对齐后端 routes/brand_voices.py(BRAND_VOICE_CONSENT_REQUIRED)。
      return err(422, "BRAND_VOICE_CONSENT_REQUIRED", "需确认授权并提供音频资源");
    }
    const id = `bv-${++brandVoiceSeq}`;
    brandVoices.set(id, { id, name: body.name || "未命名品牌音色", status: "processing", created_at: new Date(0).toISOString(), _polls: 0 });
    return ok({ id, name: body.name || "未命名品牌音色", status: "processing", created_at: new Date(0).toISOString() });
  }),
  http.delete(`${BASE}/api/v1/brand-voices/:id`, ({ params }) => {
    const id = params.id as string;
    // 未知 id → 404，与同仓 videos/copy-drafts DELETE 一致，忠实后端 NOT_FOUND 语义。
    if (!brandVoices.has(id)) return err(404, "BRAND_VOICE_NOT_FOUND", "品牌音色不存在");
    brandVoices.delete(id);
    return ok({ deleted: true });
  }),

  // ── 深度合成标识设置 (LABEL-UI-0001) ──
  // 忠实契约：GET 返 {position,text,enabled:true}；PUT 仅收 {position,text}，校验 text 非空 ≤20
  // (否则 422)；enabled 恒强制 true(client 无法关闭)。非伪造。
  http.get(`${BASE}/api/v1/tenant/label-settings`, () => ok({ ...labelSettings })),
  http.put(`${BASE}/api/v1/tenant/label-settings`, async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as { position?: string; text?: string };
    const text = (body.text ?? "").trim();
    if (!text || text.length > 20) {
      return err(422, "LABEL_TEXT_INVALID", "标识文案需为 1–20 个非空字符");
    }
    // position 枚举校验，对齐后端 Literal(不放宽，避免 mock 掩盖契约)。
    if (body.position && !["br", "bl", "tr", "tl", "bc"].includes(body.position)) {
      return err(422, "LABEL_POSITION_INVALID", "标识位置非法");
    }
    if (body.position) labelSettings.position = body.position;
    labelSettings.text = text;
    labelSettings.enabled = true; // 合规：强制恒真，忽略任何关闭意图
    return ok({ ...labelSettings });
  })
];
