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
// 图片资产上传序号：每次 /uploads/images 返唯一 asset_id（贴近真后端 uuid），避免多图碰撞同 id。
let imageUploadSeq = 0;

// ── 深度合成标识设置 (LABEL-UI-0001) mock store ──
// 忠实契约：enabled 只读恒真(合规不可关)；PUT 校验 text 非空 ≤20(否则 422)；非伪造。
const labelSettings = { position: "br", text: "AI 生成", enabled: true };

// ── 发布中心 (PUBLISH-UI-0001) mock store ── 逐字对齐后端 schemas/publish.py。
// platforms(id/name/title_max/publish_url/cover_ratio/notes)、drafts(返 {id,items})、records(嵌套
// {id,source,platforms[]{platform_id,status}})、PATCH 标记单平台已发布、DELETE；status draft/copied/published。
const PUBLISH_PLATFORMS = [
  { id: "douyin", name: "抖音", title_max: 55, body_max: 1000, hashtag_max: 5, publish_url: "https://creator.douyin.com/", cover_ratio: "3:4", notes: "标题控制在55字以内，建议3-5个话题。" },
  { id: "kuaishou", name: "快手", title_max: 50, body_max: 1000, hashtag_max: 5, publish_url: "https://cp.kuaishou.com/", cover_ratio: "3:4", notes: "标题简短直接，建议3-5个话题。" },
  { id: "wxchannels", name: "视频号", title_max: 30, body_max: 1000, hashtag_max: 3, publish_url: "https://channels.weixin.qq.com/", cover_ratio: "3:4", notes: "短标题、少量话题，表达克制。" },
  { id: "xiaohongshu", name: "小红书", title_max: 20, body_max: 1000, hashtag_max: 10, publish_url: "https://creator.xiaohongshu.com/", cover_ratio: "3:4", notes: "标题20字以内，正文更完整，话题更丰富。" },
  { id: "bilibili", name: "B站", title_max: 80, body_max: 2000, hashtag_max: 5, publish_url: "https://member.bilibili.com/platform/upload/video/frame", cover_ratio: "16:9", notes: "标题可更完整，正文适合补充分区与简介信息。" }
];
const PUBLISH_IDS = PUBLISH_PLATFORMS.map((p) => p.id);
// 记录：嵌套模型(id + 产物 + platforms[]{platform_id,status})。
const publishRecords = new Map<string, { id: string; source_kind: string; source_task_id: string; created_at: string; platforms: { platform_id: string; status: string }[] }>();
let publishSeq = 0;

// ── 视频生成 配乐库 (VIDEOGEN-UI-0001) mock ── 对齐 seam §3：免版权预置曲，preview_url 可试听。
const BGM_LIBRARY = [
  { track_id: "bgm-uplift", name: "轻快上扬", duration_sec: 30, preview_url: "https://mock.local/bgm/uplift.mp3", license: "CC0" },
  { track_id: "bgm-calm", name: "舒缓氛围", duration_sec: 45, preview_url: "https://mock.local/bgm/calm.mp3", license: "CC0" },
  { track_id: "bgm-energetic", name: "动感节奏", duration_sec: 60, preview_url: "https://mock.local/bgm/energetic.mp3", license: "CC0" }
];
const BGM_TRACK_IDS = BGM_LIBRARY.map((t) => t.track_id);
const VIDEO_GEN_DURATIONS = [5, 10, 15];
const VIDEO_GEN_RESOLUTIONS = ["480p", "720p", "1080p"];

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

// ── 批量生产中心 (BATCH-PROD-UI-0001) mock store ── 据 seam 冻结契约；后端合后对齐。
// per_row 按分辨率(mock 值，真值待后端)；balance 固定便于测 insufficient；detail 轮询状态渐进。
const BATCH_PER_ROW: Record<string, number> = { "480p": 1, "720p": 2, "1080p": 5 };
const BATCH_BALANCE = 50; // mock 余额：便于跨小/大批测 充足/不足 边界
// common extra=forbid 白名单（逐字对齐后端 BatchCommonParams）。
const BATCH_COMMON_KEYS = ["video_mode", "duration_sec", "resolution", "reference_image_asset_ids", "bgm", "voice_id", "speed", "aspect_ratio", "subtitle_enabled", "apply_visible_label", "size"];
type MockBatchTask = { task_id: string; row_index: number; status: string; video_url?: string | null; error?: string | null; error_code?: string | null; error_message?: string | null };
type MockBatch = { id: string; kind: string; status: string; total: number; succeeded: number; failed: number; common_params: Record<string, unknown>; created_at: string; updated_at: string; tasks: MockBatchTask[]; _polls: number; _cancelled: boolean };
const batchStore = new Map<string, MockBatch>();
let batchSeq = 0;

function batchPerRow(resolution?: string): number {
  return BATCH_PER_ROW[resolution ?? "720p"] ?? 2;
}
// 行必填校验(对齐后端 services/batches.py)：ecom 行 product_name/selling_points 必填 +
// image_asset_id 与 image_url **恰好二选一**(都给/都不给=非法)；prompt 行 prompt 必填。→ 非法即 BATCH_ROW_INVALID。
function invalidBatchRows(kind: string, rows: unknown[]): boolean {
  if (!Array.isArray(rows) || rows.length < 1 || rows.length > 30) return true;
  return rows.some((r) => {
    const row = r as Record<string, unknown>;
    if (kind === "ecom_table") {
      if (!row.product_name || !row.selling_points) return true;
      return Boolean(row.image_asset_id) === Boolean(row.image_url); // XOR：恰好一个
    }
    return !row.prompt || String(row.prompt).trim() === "";
  });
}
// common 校验(对齐后端 extra=forbid + video_mode 必填 + kind↔video_mode 强校验)：非法即 FastAPI VALIDATION_ERROR。
function invalidBatchCommon(kind: string, common: unknown): boolean {
  const c = (common ?? {}) as Record<string, unknown>;
  if (Object.keys(c).some((k) => !BATCH_COMMON_KEYS.includes(k))) return true; // 多传字段
  if (!c.video_mode) return true; // video_mode 必填
  if (kind === "ecom_table" && c.video_mode !== "seedance_i2v") return true;
  if (kind === "prompt_set" && c.video_mode !== "video_gen") return true;
  return false;
}
// BatchSummary 形状（逐字对齐后端：含 common_params）。
function summaryOf(b: MockBatch) {
  return { id: b.id, kind: b.kind, status: b.status, total: b.total, succeeded: b.succeeded, failed: b.failed, common_params: b.common_params, created_at: b.created_at, updated_at: b.updated_at };
}
// 聚合批次状态。
function aggregateBatchStatus(tasks: MockBatchTask[]): string {
  const done = tasks.filter((t) => t.status === "done").length;
  const failed = tasks.filter((t) => t.status === "failed").length;
  const cancelled = tasks.filter((t) => t.status === "cancelled").length;
  const active = tasks.some((t) => t.status === "queued" || t.status === "running");
  if (active) return "running";
  if (cancelled === tasks.length) return "cancelled";
  if (failed === 0) return "completed";
  if (done === 0) return "failed";
  return "partial_failed";
}

// ── 管理员数据看板 mock 数据 (ANALYTICS-UI-0001) ── 确定性生成，供 by-tenant 排序/分页真实生效。
const ANALYTICS_TENANTS = Array.from({ length: 46 }, (_, i) => {
  const total = 1000 + i * 25;
  const used = Math.round(total * (0.2 + ((i * 7) % 60) / 100));
  const reserved = Math.round(total * (((i * 3) % 20) / 100));
  const credits_used = Math.round((total - i * 3) * 10) / 10;
  const success = 40 + ((i * 13) % 160);
  const failed = 2 + ((i * 5) % 30);
  return {
    tenant_id: `ten-${String(i + 1).padStart(3, "0")}`,
    tenant_name: `租户 ${i + 1}`,
    credits_used,
    cost_cents: 1500 + i * 137,
    task_count: success + failed,
    success_rate: Math.round((success / (success + failed)) * 1000) / 1000,
    balance: { total, used, reserved, remaining: Math.max(0, total - used - reserved) }
  };
});

function analyticsForbidden(): boolean {
  try {
    return typeof localStorage !== "undefined" && localStorage.getItem("hd_mock_non_admin") === "1";
  } catch {
    return false;
  }
}

function analyticsHandlers() {
  const A = `${BASE}/api/v1/admin/analytics`;
  const guard = () => (analyticsForbidden() ? err(403, "FORBIDDEN", "Insufficient permission.") : null);
  const sortTenants = (sort: string) => {
    const field = sort.replace(/_(asc|desc)$/, "");
    const dir = sort.endsWith("_asc") ? 1 : -1;
    const key: Record<string, (t: (typeof ANALYTICS_TENANTS)[number]) => number> = {
      credits: (t) => t.credits_used,
      cost: (t) => t.cost_cents,
      task_count: (t) => t.task_count,
      success_rate: (t) => t.success_rate
    };
    const fn = key[field] ?? key.credits;
    return [...ANALYTICS_TENANTS].sort((a, b) => (fn(a) - fn(b)) * dir);
  };
  return [
    http.get(`${A}/overview`, ({ request }) => {
      const g = guard();
      if (g) return g;
      const url = new URL(request.url);
      return ok({
        total_credits_used: 48213.5,
        total_cost_cents: 1892340,
        task_count: 5230,
        success_count: 4890,
        failed_count: 340,
        tenant_count: ANALYTICS_TENANTS.length,
        period: { from: url.searchParams.get("from") ?? "", to: url.searchParams.get("to") ?? "" }
      });
    }),
    http.get(`${A}/by-tenant`, ({ request }) => {
      const g = guard();
      if (g) return g;
      const url = new URL(request.url);
      const sort = url.searchParams.get("sort") ?? "credits_desc";
      const limit = Math.min(100, Math.max(1, Number(url.searchParams.get("limit") ?? 20)));
      const offset = Math.max(0, Number(url.searchParams.get("offset") ?? 0));
      const sorted = sortTenants(sort);
      return ok({ items: sorted.slice(offset, offset + limit), total: ANALYTICS_TENANTS.length });
    }),
    http.get(`${A}/by-provider`, () => {
      const g = guard();
      if (g) return g;
      return ok({
        items: [
          { provider: "seedance", model: "i2v-v1", credits_used: 21500, cost_cents: 812000, task_count: 2100, share_pct: 44.6 },
          { provider: "video_gen", model: "vg-pro", credits_used: 15200, cost_cents: 540300, task_count: 1630, share_pct: 31.5 },
          { provider: "copywriting", model: null, credits_used: 6800, cost_cents: 210400, task_count: 980, share_pct: 14.1 },
          { provider: "image", model: "flux-1", credits_used: 4713.5, cost_cents: 329640, task_count: 520, share_pct: 9.8 }
        ]
      });
    }),
    http.get(`${A}/timeseries`, ({ request }) => {
      const g = guard();
      if (g) return g;
      const url = new URL(request.url);
      const granularity = url.searchParams.get("granularity") ?? "day";
      const from = url.searchParams.get("from") ?? "";
      const step = granularity === "week" ? 7 : 1;
      const count = granularity === "week" ? 6 : 14;
      const base = from ? new Date(`${from}T00:00:00`) : new Date("2026-06-01T00:00:00");
      const buckets = Array.from({ length: count }, (_, i) => {
        const d = new Date(base);
        d.setDate(d.getDate() + i * step);
        const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
        return {
          date: iso,
          credits_used: Math.round((800 + Math.sin(i) * 300 + i * 40) * 10) / 10,
          cost_cents: 30000 + i * 4200 + (i % 3) * 1500,
          task_count: 120 + i * 9 + (i % 4) * 15
        };
      });
      return ok({ buckets });
    })
  ];
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
  http.post(`${BASE}/api/v1/uploads/images`, () => {
    const n = ++imageUploadSeq;
    return HttpResponse.json(
      { data: { asset_id: `upload-${n}`, type: "avatar_image", status: "ready", thumbnail_url: `https://mock.local/u${n}.jpg` }, error: null, request_id: "mock-req" },
      { status: 201 }
    );
  }),
  // 产品图上传（电商带货 i2v）→ 后端 POST /uploads 返 UploadResponse{key,uri}(201)，前端映射 key→image_key。
  // 此前 mock 缺该端点致电商 tab 无法真栈冒烟（ECOM-RESOLUTION-UI-0001 顺带补齐，忠实真契约）。
  http.post(`${BASE}/api/v1/uploads`, () => {
    const n = ++imageUploadSeq;
    return HttpResponse.json(
      { data: { key: `uploads/mock-product-${n}.png`, uri: `mock://uploads/mock-product-${n}.png` }, error: null, request_id: "mock-req" },
      { status: 201 }
    );
  }),
  http.post(`${BASE}/api/v1/videos`, async ({ request }) => {
    const body = (await request.json()) as {
      topic?: string;
      video_mode?: string;
      purpose?: string;
      prompt?: string;
      reference_image_asset_ids?: string[];
      duration_sec?: number;
      resolution?: string;
      bgm?: { source?: string; asset_id?: string; track_id?: string };
      apply_visible_label?: boolean;
    };
    // resolution 是后端全模式 Literal["480p","720p","1080p"]（含 seedance_i2v，见 schemas/videos.py:154）：
    // 非法即 422，不按模式放宽（ECOM-RESOLUTION-UI-0001：电商也带 resolution，需与 video_gen 同等把关，不伪造放行）。
    if (body.resolution !== undefined && !VIDEO_GEN_RESOLUTIONS.includes(body.resolution)) {
      return err(422, "VALIDATION_ERROR", "resolution 非法");
    }
    // 视频生成 video_gen 校验（逐字对齐后端 schemas/videos.py:213-222：参考图 1–9 且**唯一**、prompt
    // 非空、duration∈{5,10,15}、bgm 二选一可选）→ 非法 422，不伪造放行/不放宽（resolution 已在上方全模式把关）。
    if (body.video_mode === "video_gen") {
      const refs = body.reference_image_asset_ids ?? [];
      const refsUnique = new Set(refs).size === refs.length; // 对齐后端唯一性校验（重复→422）
      const bgmOk =
        body.bgm === undefined ||
        (body.bgm.source === "upload" && !!body.bgm.asset_id) ||
        (body.bgm.source === "library" && !!body.bgm.track_id && BGM_TRACK_IDS.includes(body.bgm.track_id));
      if (
        !Array.isArray(refs) ||
        refs.length < 1 ||
        refs.length > 9 ||
        !refsUnique ||
        !body.prompt ||
        !body.prompt.trim() ||
        !VIDEO_GEN_DURATIONS.includes(body.duration_sec as number) ||
        !bgmOk
      ) {
        return err(422, "VIDEO_GEN_INVALID", "视频生成参数非法");
      }
    }
    const id = `mock-${++videoSeq}`;
    // video_gen 用 prompt 作展示标题；记 mode + kind(AI 封面 purpose=cover → kind=cover)，让 GET /videos 筛忠实回放
    const displayTopic = body.video_mode === "video_gen" ? (body.prompt ?? "") : (body.topic ?? "");
    videos.set(id, { id, status: "queued", progress: 0, topic: displayTopic, mode: body.video_mode ?? "avatar_talk", kind: body.purpose === "cover" ? "cover" : null, created_at: new Date(0).toISOString(), script: displayTopic, voice_id: "v-zhixing", aspect_ratio: "9:16", subtitle_enabled: true, apply_visible_label: body.apply_visible_label ?? false });
    return HttpResponse.json({ data: { id, status: "queued" }, error: null, request_id: "mock-req" }, { status: 202 });
  }),
  // 视频生成 配乐库 (VIDEOGEN-UI-0001, seam §3)
  http.get(`${BASE}/api/v1/bgm-library`, () => ok({ items: BGM_LIBRARY })),
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
    const body = (await request.json()) as { source_asset_id: string; background: string; apply_visible_label?: boolean };
    const id = `mock-${++videoSeq}`;
    const url =
      body.background === "transparent"
        ? "https://mock.local/cutout-transparent.png"
        : "https://mock.local/cutout-white.png";
    videos.set(id, {
      id, status: "done", progress: 100, topic: body.background === "transparent" ? "透明底商品图" : "白底商品图",
      mode: "photo", kind: "ecom_cutout", created_at: new Date(0).toISOString(),
      playback_url: url, download_url: `${url}?dl=1`, thumbnail_url: url, apply_visible_label: body.apply_visible_label ?? false
    });
    return ok({ task_id: id, status: "queued" });
  }),
  http.post(`${BASE}/api/v1/ecom-images/cutout/batch`, async ({ request }) => {
    const body = (await request.json()) as { items: { source_asset_id: string; background: string; apply_visible_label?: boolean }[] };
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
        playback_url: url, download_url: `${url}?dl=1`, thumbnail_url: url, apply_visible_label: it.apply_visible_label ?? false
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
    const body = (await request.json()) as { source_asset_id: string; gender: string; style_id: string; apply_visible_label?: boolean };
    const id = `mock-${++videoSeq}`;
    const url = `https://mock.local/model-${body.style_id || "studio"}.png`;
    videos.set(id, {
      id, status: "done", progress: 100, topic: "AI 模特图",
      mode: "photo", kind: "ecom_model", created_at: new Date(0).toISOString(),
      playback_url: url, download_url: `${url}?dl=1`, thumbnail_url: url, apply_visible_label: body.apply_visible_label ?? false
    });
    return ok({ task_id: id, status: "queued" });
  }),
  http.post(`${BASE}/api/v1/ecom-images/model/batch`, async ({ request }) => {
    const body = (await request.json()) as { items: { source_asset_id: string; style_id: string; apply_visible_label?: boolean }[] };
    const items = (body.items ?? []).slice(0, 20); // N clamp 上界 20
    const batchId = `batch-${++videoSeq}`;
    const tasks = items.map((it) => {
      const id = `mock-${++videoSeq}`;
      const url = `https://mock.local/model-${it.style_id || "studio"}.png`;
      videos.set(id, {
        id, status: "done", progress: 100, topic: "批量 AI 模特",
        mode: "photo", kind: "ecom_model", created_at: new Date(0).toISOString(),
        playback_url: url, download_url: `${url}?dl=1`, thumbnail_url: url, apply_visible_label: it.apply_visible_label ?? false
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
    const body = (await request.json()) as { source_asset_id: string; template_id: string; apply_visible_label?: boolean };
    const id = `mock-${++videoSeq}`;
    const url = `https://mock.local/poster-${body.template_id || "promo_bold"}.png`;
    videos.set(id, {
      id, status: "done", progress: 100, topic: "营销海报",
      mode: "photo", kind: "ecom_poster", created_at: new Date(0).toISOString(),
      playback_url: url, download_url: `${url}?dl=1`, thumbnail_url: url, apply_visible_label: body.apply_visible_label ?? false
    });
    return ok({ task_id: id, status: "queued" });
  }),
  http.post(`${BASE}/api/v1/ecom-images/poster/batch`, async ({ request }) => {
    const body = (await request.json()) as { items: { source_asset_id: string; template_id: string; apply_visible_label?: boolean }[] };
    const items = (body.items ?? []).slice(0, 20); // N clamp 上界 20
    const batchId = `batch-${++videoSeq}`;
    const tasks = items.map((it) => {
      const id = `mock-${++videoSeq}`;
      const url = `https://mock.local/poster-${it.template_id || "promo_bold"}.png`;
      videos.set(id, {
        id, status: "done", progress: 100, topic: "批量营销海报",
        mode: "photo", kind: "ecom_poster", created_at: new Date(0).toISOString(),
        playback_url: url, download_url: `${url}?dl=1`, thumbnail_url: url, apply_visible_label: it.apply_visible_label ?? false
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
    // position 必填 + 枚举校验，逐字对齐后端 TenantLabelSettingsUpdate.position(Literal，必填)：
    // 缺失/空/非法均 422(不放宽“present 才校验”，避免 mock 掩盖前端漏发 position 的回归)。
    if (!body.position || !["br", "bl", "tr", "tl", "bc"].includes(body.position)) {
      return err(422, "LABEL_POSITION_INVALID", "标识位置非法或缺失");
    }
    labelSettings.position = body.position; // 校验后必有，直接赋值
    labelSettings.text = text;
    labelSettings.enabled = true; // 合规：强制恒真，忽略任何关闭意图
    return ok({ ...labelSettings });
  }),

  // ── 发布中心 (PUBLISH-UI-0001) ──
  http.get(`${BASE}/api/v1/publish/platforms`, () => ok({ items: PUBLISH_PLATFORMS })),
  http.post(`${BASE}/api/v1/publish/drafts`, async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as {
      source_kind?: string;
      source_task_id?: string;
      platforms?: string[];
    };
    const ids = body.platforms ?? [];
    const sk = body.source_kind;
    const taskId = (body.source_task_id ?? "").trim();
    const unique = new Set(ids).size === ids.length;
    // 对齐后端：source_kind∈video|image、source_task_id 1–80 非空、platforms 1–5 合法且唯一，否则 422。
    if (
      (sk !== "video" && sk !== "image") ||
      !taskId ||
      taskId.length > 80 ||
      ids.length < 1 ||
      ids.length > 5 ||
      !unique ||
      ids.some((p) => !PUBLISH_IDS.includes(p))
    ) {
      return err(422, "PUBLISH_DRAFT_INVALID", "请求参数非法");
    }
    const recordId = `pub-${++publishSeq}`;
    publishRecords.set(recordId, {
      id: recordId,
      source_kind: sk,
      source_task_id: taskId,
      created_at: new Date(0).toISOString(),
      platforms: ids.map((pid) => ({ platform_id: pid, status: "draft" }))
    });
    // 返回各平台可编辑内容(PublishDraftItem)。
    const items = ids.map((pid) => {
      const plat = PUBLISH_PLATFORMS.find((x) => x.id === pid);
      const name = plat?.name ?? pid;
      return {
        platform_id: pid,
        title: `${name}·AI 短视频`,
        body: `适配${name}的发布文案，记得带上话题哦～`,
        hashtags: ["#AI生成", "#好物推荐"],
        cover_url: "https://mock.local/cover.png",
        media_url: "https://mock.local/v.mp4",
        // 去发布开的是平台官方创作页(对齐真后端 draft item.publish_url)。
        publish_url: plat?.publish_url ?? `https://mock.local/publish/${pid}`
      };
    });
    return ok({ id: recordId, items });
  }),
  http.get(`${BASE}/api/v1/publish/records`, () => {
    const items = [...publishRecords.values()];
    return ok({ items, total: items.length });
  }),
  http.patch(`${BASE}/api/v1/publish/records/:id`, async ({ request, params }) => {
    const rec = publishRecords.get(params.id as string);
    if (!rec) return err(404, "PUBLISH_RECORD_NOT_FOUND", "发布记录不存在");
    const body = (await request.json().catch(() => ({}))) as { platform_id?: string; status?: string };
    // 对齐后端 PublishRecordPatchRequest：platform_id 合法 + status 仅 "published"，否则 422。
    if (!body.platform_id || !PUBLISH_IDS.includes(body.platform_id) || body.status !== "published") {
      return err(422, "PUBLISH_PATCH_INVALID", "请求参数非法");
    }
    const target = rec.platforms.find((p) => p.platform_id === body.platform_id);
    if (!target) return err(404, "PUBLISH_PLATFORM_NOT_FOUND", "平台不在该记录内");
    target.status = "published";
    return ok({ ...rec });
  }),
  http.delete(`${BASE}/api/v1/publish/records/:id`, ({ params }) => {
    const id = params.id as string;
    if (!publishRecords.has(id)) return err(404, "PUBLISH_RECORD_NOT_FOUND", "发布记录不存在");
    publishRecords.delete(id);
    return ok({ id, deleted_at: new Date(0).toISOString() });
  }),

  // ── 批量生产中心 (BATCH-PROD-UI-0001) ── 逐字对齐后端 #103 schema：estimate/create(202)/list/detail/cancel。
  http.post(`${BASE}/api/v1/batches/estimate`, async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as { kind?: string; rows?: unknown[]; common?: { resolution?: string } };
    const rows = body.rows ?? [];
    if (invalidBatchRows(body.kind ?? "", rows)) return err(422, "BATCH_ROW_INVALID", "批次行校验失败");
    if (invalidBatchCommon(body.kind ?? "", body.common)) return err(422, "VALIDATION_ERROR", "common 参数非法（多传字段 / video_mode 缺失或不匹配 kind）");
    const perRow = batchPerRow(body.common?.resolution);
    const total = perRow * rows.length;
    return ok({ total_rows: rows.length, per_row_credits: perRow, total_credits: total, insufficient: total > BATCH_BALANCE, balance_credits: BATCH_BALANCE });
  }),
  http.post(`${BASE}/api/v1/batches`, async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as { kind?: string; rows?: unknown[]; common?: Record<string, unknown> };
    const rows = body.rows ?? [];
    if (invalidBatchRows(body.kind ?? "", rows)) return err(422, "BATCH_ROW_INVALID", "批次行校验失败");
    if (invalidBatchCommon(body.kind ?? "", body.common)) return err(422, "VALIDATION_ERROR", "common 参数非法");
    const total = batchPerRow((body.common?.resolution as string) ?? undefined) * rows.length;
    if (total > BATCH_BALANCE) return err(422, "INSUFFICIENT_CREDITS", "余额不足，无法提交本批");
    const id = `batch-${++batchSeq}`;
    const tasks: MockBatchTask[] = rows.map((_, i) => ({ task_id: `${id}-t${i}`, row_index: i, status: "queued", video_url: null, error: null, error_code: null, error_message: null }));
    batchStore.set(id, { id, kind: body.kind ?? "ecom_table", status: "running", total: rows.length, succeeded: 0, failed: 0, common_params: body.common ?? {}, created_at: new Date(0).toISOString(), updated_at: new Date(0).toISOString(), tasks, _polls: 0, _cancelled: false });
    // 后端返回 202 ACCEPTED。
    return HttpResponse.json({ data: { batch_id: id, task_ids: tasks.map((t) => t.task_id) }, error: null, request_id: "mock-req" }, { status: 202 });
  }),
  http.get(`${BASE}/api/v1/batches`, () => {
    const items = [...batchStore.values()].map(summaryOf);
    return ok({ items, total: items.length });
  }),
  http.get(`${BASE}/api/v1/batches/:id`, ({ params }) => {
    const b = batchStore.get(params.id as string);
    if (!b) return err(404, "BATCH_NOT_FOUND", "批次不存在");
    if (!b._cancelled) {
      // 轮询渐进：queued→running→done；末条(total>1)置 failed 演示 partial(v1 无单条重试端点，仅展示失败原因)。
      b._polls += 1;
      b.tasks = b.tasks.map((t, i) => {
        if (t.status === "cancelled" || t.status === "done" || t.status === "failed") return t;
        if (b._polls >= i + 2) {
          const isLastFailing = b.total > 1 && i === b.total - 1;
          return isLastFailing
            ? { ...t, status: "failed", error: "生成失败", error_code: "BATCH_IMAGE_DOWNLOAD_FAILED", error_message: "外链图下载失败，请改用本地上传" }
            : { ...t, status: "done", video_url: "https://mock.local/v.mp4" };
        }
        if (b._polls >= i + 1) return { ...t, status: "running" };
        return t;
      });
      b.succeeded = b.tasks.filter((t) => t.status === "done").length;
      b.failed = b.tasks.filter((t) => t.status === "failed").length;
      b.status = aggregateBatchStatus(b.tasks);
      b.updated_at = new Date(0).toISOString();
    }
    return ok({ batch: summaryOf(b), tasks: b.tasks });
  }),
  http.post(`${BASE}/api/v1/batches/:id/cancel`, ({ params }) => {
    const b = batchStore.get(params.id as string);
    if (!b) return err(404, "BATCH_NOT_FOUND", "批次不存在");
    const cancelled = b.tasks.filter((t) => t.status === "queued").length;
    const running = b.tasks.filter((t) => t.status === "running").length;
    b.tasks = b.tasks.map((t) => (t.status === "queued" ? { ...t, status: "cancelled" } : t)); // 已跑不中断(best-effort)
    b._cancelled = true; // 停止轮询渐进
    b.status = aggregateBatchStatus(b.tasks);
    // 后端 BatchCancelResponse = {batch_id, cancelled, running}。
    return ok({ batch_id: b.id, cancelled, running });
  }),

  // ── 管理员数据看板 (ANALYTICS-UI-0001) ── /api/v1/admin/analytics/*，require_admin。
  // 非管理员模拟：localStorage["hd_mock_non_admin"]==="1" → 403 FORBIDDEN（供 Playwright 403 态验证）。
  ...analyticsHandlers()
];
