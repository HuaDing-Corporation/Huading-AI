import { http, HttpResponse } from "msw";

import { aibrainHandlers } from "./aibrain-handlers"; // 华鼎AI智脑（AIBRAIN-UI-0001）——独立段落，追加在数组末尾
import { registerMockAsset } from "./asset-registry"; // FIX4：上传登记资产，智脑发消息查表（唯一资产来源）

// Mirror client.ts's trailing-slash normalization so handler URLs always match
// what apiFetch requests (avoids a latent "mock silently bypassed" footgun).
const BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const ok = <T>(data: T) => HttpResponse.json({ data, error: null, request_id: "mock-req" });
const err = (status: number, code: string, message: string) =>
  HttpResponse.json({ data: null, error: { code, message, request_id: "mock-req" }, request_id: "mock-req" }, { status });

// ECOM-VIDEO-SCENE-DURATION-FIX-UI-0001 · FIX1（CB P1）：真 BE duration_sec 是 int（ScenePromptRequest /
// VideoGenerateRequest 皆然），拒绝小数(5.5/5.4)与字符串("5.5")。mock 不比 BE 宽松、不四舍五入放行——present 且
// 非整数即非法（调用处返 422）；undefined/null（可选未传）不算非法。scene-prompt / estimate / videos 提交三处共用。
const badDuration = (v: unknown): boolean => v !== undefined && v !== null && !Number.isInteger(v);

// IMAGE-GEN-OPTIMIZE-UI-0001 §四：三个强度取值 10..100 步长 10，None=未开启（背景参考强度已砍除·从未上线）。present 且非「10..100 步10 整数」即非法。
const badStrength = (v: unknown): boolean =>
  v !== undefined && v !== null && !(typeof v === "number" && Number.isInteger(v) && v >= 10 && v <= 100 && v % 10 === 0);

// FIX1 真联调：逐字对齐 BE schemas/videos.py `_IMAGE_KEY_RE`——参考图 key 须为 POST /uploads 返回的 uploads/<name>.{jpg,jpeg,png,webp}。
// mock 此前只卡数量不卡格式（比 BE 宽松 → 「a」这类假 key 假绿），本轮收紧防漂移。
const PHOTO_IMAGE_KEY_RE = /^uploads\/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)$/;
// photo 四层提示词各 ≤20000（BE schema 校验，超限 422，非静默截断）。
const PHOTO_PROMPT_MAX = 20000;
const overLen = (v: unknown): boolean => typeof v === "string" && v.length > PHOTO_PROMPT_MAX;

// in-memory store so list/detail/SSE stay consistent within a session
const videos = new Map<string, Record<string, unknown>>();
// mock 种子（ECOM-FIXES-0001 ③ / cancelled 补 ECOM-HISTORY-CANCELLED-FIX-0001 ③）：预置电商(seedance_i2v)历史项，
// 覆盖 TaskCard **全 5 状态**分支（done 播放器/时长/AI标识、running 进度、failed 错误+重试、queued、**cancelled 已取消**），
// 供「电商视频历史」交互冒烟真点后渲染 TaskCard、堵 #130 白屏回归。⚠️ cancelled 是这次白屏真因（上次没 seed 才漏）。仅 mock 生效。
const ECOM_HISTORY_SEED = [
  { id: "seed-ecom-done", status: "done", progress: 100, topic: "保温杯带货", mode: "seedance_i2v", kind: null, created_at: new Date(0).toISOString(), playback_url: "https://mock.local/v.mp4", download_url: "https://mock.local/v.mp4", thumbnail_url: "https://mock.local/t.jpg", duration_ms: 30000, apply_visible_label: true, voice_id: "v-zhixing", aspect_ratio: "9:16", subtitle_enabled: true },
  { id: "seed-ecom-running", status: "running", progress: 55, topic: "雨伞带货", mode: "seedance_i2v", kind: null, created_at: new Date(0).toISOString(), apply_visible_label: false, voice_id: "v-zhixing", aspect_ratio: "9:16", subtitle_enabled: true },
  { id: "seed-ecom-failed", status: "failed", progress: 100, topic: "台灯带货", mode: "seedance_i2v", kind: null, created_at: new Date(0).toISOString(), error_message: "Error code: 504 - upstream timeout (raw)", error_code: "VIDEO_TIMEOUT", apply_visible_label: false, voice_id: "v-zhixing", aspect_ratio: "9:16", subtitle_enabled: true },
  { id: "seed-ecom-queued", status: "queued", progress: 0, topic: "水杯带货", mode: "seedance_i2v", kind: null, created_at: new Date(0).toISOString(), apply_visible_label: false, voice_id: "v-zhixing", aspect_ratio: "9:16", subtitle_enabled: true },
  { id: "seed-ecom-cancelled", status: "cancelled", progress: 100, topic: "手电筒带货", mode: "seedance_i2v", kind: null, created_at: new Date(0).toISOString(), apply_visible_label: false, voice_id: "v-zhixing", aspect_ratio: "9:16", subtitle_enabled: true }
];
for (const v of ECOM_HISTORY_SEED) videos.set(v.id, v);
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
  provider?: string; // canonical 长值（镜像 BE read 侧 _brand_voice_read）；部分项**故意缺省**以验证前端兼容不显徽标
}
// FE-INTEGRATION-0001 对齐真栈：BE read 侧 provider 返 canonical 长值（doubao-voice-clone / cosyvoice-voice-clone），
// 请求侧收短值 Literal["doubao","cosyvoice"]（routes/brand_voices.py::_VOICE_CLONE_PROVIDER_ALIASES 归一化）。
const VOICE_CLONE_CANONICAL: Record<string, string> = {
  doubao: "doubao-voice-clone",
  cosyvoice: "cosyvoice-voice-clone"
};
const brandVoices = new Map<string, MockBrandVoice>([
  // provider：ready 项分别带 doubao/cosyvoice 的 canonical（picker 徽标 豆包/CosyVoice）；failed 项无 provider（picker 中隐藏，兼容缺省）。
  ["bv-ready-1", { id: "bv-ready-1", name: "我的主播音", status: "ready", created_at: new Date(0).toISOString(), _polls: 99, provider: "doubao-voice-clone" }],
  ["bv-ready-2", { id: "bv-ready-2", name: "免费复刻音", status: "ready", created_at: new Date(0).toISOString(), _polls: 99, provider: "cosyvoice-voice-clone" }],
  ["bv-failed-1", { id: "bv-failed-1", name: "失败样例", status: "failed", created_at: new Date(0).toISOString(), _polls: 99 }]
]);
let brandVoiceSeq = 0;
let audioAssetSeq = 0;
// 图片资产上传序号：每次 /uploads/images 返唯一 asset_id（贴近真后端 uuid），避免多图碰撞同 id。
let imageUploadSeq = 0;

// ── 提示词反推 (REVERSE-PROMPT-UI) mock ── FIX1：**镜像 BE 真形状**（backend schemas/reverse_prompt.py：
// ReversePromptJobRead + 真 ReversePromptResult + fill_targets 6 键内层字段一字不差）。成功 status="succeeded"、
// 含 result；6 键齐备 → 结果页「带入」六路全亮，交互冒烟可逐一验证落点。⚠️ 不再自造 jobId/ecom_image（Codex B P1）。
let reverseSeq = 0;
const REVERSE_RESULT = {
  target_format: "seedance_2_0",
  prompt_zh: "白色大理石台面上的便携保温杯，暖色晨光，浅景深特写，产品广告风格，缓慢环绕运镜，蒸汽轻升。",
  prompt_en:
    "A portable insulated bottle on a white marble countertop, warm morning light, shallow depth of field, product-ad style, slow orbiting camera, gentle rising steam.",
  negative_prompt: "低分辨率, 变形, 多余文字, 水印, 杂乱背景",
  style_tags: ["产品广告", "极简", "高级质感"],
  camera: "35mm 定焦，微俯拍",
  lighting: "柔和暖光，右上主光",
  composition: "居中特写，浅景深",
  subject: "白色大理石台面上的便携保温杯",
  scene: "室内桌面，暖色晨光",
  motion_hint: "缓慢环绕运镜，蒸汽轻升",
  selling_points: ["24 小时保温", "便携轻巧", "食品级内胆"],
  text_in_media: ["24H"],
  disclaimer: "AI 依据画面近似重建提示词，仅供二次创作参考，不保证完全复刻原素材。",
  confidence: 0.82,
  fill_targets: {
    avatar_talk: { topic: "便携保温杯种草", script: "大家好，今天给大家安利这款便携保温杯，24 小时保温，出门必备……" },
    seedance_i2v: { topic: "便携保温杯卖点", scene_prompt: "白色大理石台面暖光特写，蒸汽轻升，缓慢环绕运镜" },
    video_gen: { topic: "便携保温杯", prompt: "白色大理石台面上的保温杯，暖色晨光，缓慢环绕运镜，产品广告风格" },
    photo: { topic: "白色大理石台面上的保温杯，暖色晨光，浅景深特写" },
    ecom_model: { extra_prompt: "工作室柔光、简洁白底、突出质感" },
    ecom_poster: { title: "年中大促", subtitle: "限时 5 折 错过再等一年" }
  }
};
// ReversePromptJobRead 全字段（前端只读 id/status/result/error_*，其余照给真形状）。
// （反推 job 的读模型见下方「单一权威 job 存储」——原先这里有个 reverseJobRead(id) **合成器**：它不查任何存储、
//  凭一个 id 就现编一份 succeeded job。那正是 FIX3 要拆掉的东西，见 reverseJobs 的注释。）

// ── 视频反推异步 (VIDEO-REVERSE-PROMPT-UI-0001 · FIX2 逐字段对齐已合入真 BE schema) mock ──
// POST /reverse-prompt 检测视频源(source_asset_id 以 "video-" 起)→ 202 status="queued"(无 result)；GET /jobs/{id}
// 轮询第 2 次起 → succeeded + result（video_analysis **内嵌于 result**）。图片源仍同步 succeeded（零回归）。
// 对齐 backend/app/schemas/reverse_prompt.py：pacing=Literal["slow","medium","fast","variable"]（枚举，非中文串）；
// 每个 shot 必含 index(≥0)；shot 字段 index/start_sec/end_sec/visual/camera/motion/transition；
// credits=provider 引擎成本（非 100；租户固定 100 走 UsageRecord，此处不体现）。
const REVERSE_VIDEO_ANALYSIS = {
  duration_sec: 18,
  pacing: "fast", // 合法枚举（前端映射为「快」显示）
  shot_list: [
    { index: 0, start_sec: 0, end_sec: 4, visual: "产品特写：保温杯置于大理石台面，暖光扫过", camera: "缓慢推近", motion: "蒸汽轻升", transition: "叠化" },
    { index: 1, start_sec: 4, end_sec: 10, visual: "使用场景：手部拧开杯盖，蒸汽升腾", camera: "手持跟拍", motion: "手部拧盖", transition: "硬切" },
    { index: 2, start_sec: 10, end_sec: 15, visual: "卖点字幕叠加：24 小时保温，便携轻巧", camera: "固定机位", motion: "字幕入场", transition: "淡出" },
    { index: 3, start_sec: 15, end_sec: 18, visual: "收尾定格：品牌 logo + 行动号召", camera: "环绕收尾", motion: "logo 定格", transition: "定格" }
  ],
  audio_transcript: null, // 一期未启用
  bgm_style: null // 一期未启用
};
// （视频 job 不再有独立 Map —— 见下方「单一权威 job 存储」。）

// ── 反推历史 (HISTORY-VIDEO-REVERSE-UI-0001) mock ──
// **镜像已合入 develop 的真 BE**（逐字段核对 backend/app/schemas/reverse_prompt.py:92-110 与
// routes/reverse_prompt.py:64-87 / :152-166、services/reverse_prompt.py），关键事实（与摘要不同，以源码为准）：
//   ① 列表项**只有 6 个字段、无 result** → 「带入」拿不到 fill_targets，必须先取详情；
//   ② source_thumbnail_url **仅 image 源有值，video 源恒 null**（services:320-322）；
//   ③ summary = prompt_zh → subject → prompt_en 首个非空、截断 160（services:332-339）；
//   ④ status 取值 = DB CheckConstraint 5 值 queued/running/succeeded/failed/saved（models.py:471）；
//   ⑤ DELETE 是**纯软删**（只置 deleted_at、不碰媒体）；🔴 FIX3 修正：列表过滤已删，**详情同样 404**
//      —— 详情/regenerate/save/重复删除全走 reverse_prompt_job_or_404，它用的是 select_live_reverse_prompt_jobs
//      （services:365-368）= `deleted_at IS NULL`。（此处原注释写「详情仍可取（services:276）」是**错的**：
//      :276 是**列表**的过滤条件，拿它去断言详情的行为是张假路标，FIX2 已修代码、FIX3 补修这句注释。）
//
// ══ 🔴 FIX3 · 单一权威 job 存储 ══════════════════════════════════════════════════════════════
// 上一版这里有**三个状态源**：`reverseHistory[]`（历史 seed）、`reverseVideoJobs` Map（POST 视频 job）、
// 以及 `reverseJobRead(id)` —— 一个**连存储都没有的合成器**：POST 图片反推产生的 `rp-*` job 从来没被存下来过，
// 所以 GET/regenerate/save 拿到 `rp-*` 时**只能猜**。「rp-* 一律放行」不是疏忽，是**没得选**。
//
// 于是每个 handler 都在自己编「什么该成功」，编出来的还互相矛盾 —— 实例：DELETE 返 `REVERSE_JOB_NOT_FOUND`、
// GET 返 `REVERSE_PROMPT_JOB_NOT_FOUND`，同一个 BE 错误两个码（BE 真值是后者）。**补断言补不完，因为下一个
// handler 还会再猜一次。**
//
// 与 #164 同构（CLAUDE.md 第三十四轮：mock 收敛为单一状态源 `resolveMockState()`，注册只写一处、
// `/me` + analytics + doubao 全走它）—— 那次 Codex B 连打四轮才收敛，结论是：
// **mock 有多个状态源 → 各自宽松 → 假绿。唯一的解是单一权威存储。**
//
// 本 store 的权威性体现在三点：
//   ① **唯一写入口**：历史 seed、POST 图片 job、POST 视频 job 全部 `reverseJobs.set(...)`，没有第二个地方造 job；
//   ② **唯一读守卫**：`liveJob(id)` 是 GET/regenerate/save/delete 取 job 的**唯一**途径，存在性与软删只在这一处判；
//   ③ **唯一序列化**：`reverseJobRead(job)` 一个函数吃所有 job（图片/视频、五种状态），没有第二份读模型。
// 任一 handler 想「特殊照顾」某个 id，都得先绕过这三点 —— 而绕过是显眼的。
//
// ⚠️ 关于「同租户」：mock 只服务一个租户，租户来自 auth token、前端无法表达跨租户请求 →
// **跨租户在 mock 里不可表达**。加个 tenant_id 字段再自己跟自己比对是装饰，不是防线。故本 store 只守
// 「存在 + 未删除」，并在此写明这个边界 —— 而不是假装守住了 BE 的 `job.tenant_id != tenant_id → 404`。
interface MockReverseJob {
  id: string;
  source_kind: "image" | "video";
  /** DB CheckConstraint 五值（models.py:471）。 */
  status: "queued" | "running" | "succeeded" | "failed" | "saved";
  created_at: string;
  saved_at: string | null;
  /** 镜像 BE deleted_at（软删只置这个、不碰媒体）。null = live。 */
  deleted_at: string | null;
  source_thumbnail_url: string | null;
  summary: string | null;
  error_code: string | null;
  error_message: string | null;
  /** 视频异步轮询模拟：详情第 2 次读起 queued → succeeded。 */
  polls: number;
}
const revTs = (i: number) => new Date(Date.UTC(2026, 6, 16, 12, 0, 0) - i * 60_000).toISOString(); // 递减 → 倒序稳定

/** 唯一权威存储 —— 所有反推 job 都住这里，没有第二个地方。 */
const reverseJobs = new Map<string, MockReverseJob>();

const mkReverseJob = (
  seed: Pick<MockReverseJob, "id" | "source_kind"> & Partial<MockReverseJob>
): MockReverseJob => ({
  status: "succeeded",
  created_at: new Date(0).toISOString(),
  saved_at: null,
  deleted_at: null,
  source_thumbnail_url: null,
  summary: null,
  error_code: null,
  error_message: null,
  polls: 0,
  ...seed
});

/**
 * 🔴 **唯一的存在性/软删守卫** —— GET/regenerate/save/delete 取 job 必须且只能走这里。
 * 镜像 BE `reverse_prompt_job_or_404` + `select_live_reverse_prompt_jobs`（`deleted_at IS NULL`）。
 * 摘掉它 = 回到「每个 handler 各自猜」→ 对应确定值测试必红。
 */
const liveJob = (id: string): MockReverseJob | null => {
  const job = reverseJobs.get(id);
  return job && job.deleted_at === null ? job : null;
};
const jobNotFound = () => err(404, "REVERSE_PROMPT_JOB_NOT_FOUND", "记录不存在或无权访问");

const REVERSE_SEEDS: (Pick<MockReverseJob, "id" | "source_kind"> & Partial<MockReverseJob>)[] = [
  {
    id: "rh-img-1",
    source_kind: "image",
    status: "succeeded",
    created_at: revTs(0),
    source_thumbnail_url: "https://mock.local/reverse/src-1.png",
    summary: "白色大理石台面上的便携保温杯，暖色晨光，浅景深特写，产品广告风格，缓慢环绕运镜，蒸汽轻升。",
  },
  {
    id: "rh-img-2",
    source_kind: "image",
    status: "saved",
    created_at: revTs(1),
    saved_at: revTs(1), // BE 状态机 succeeded→saved 时置；原先由读模型按 status 派生，现在是 store 的真字段
    source_thumbnail_url: "https://mock.local/reverse/src-2.png",
    summary: "陶瓷马克杯静物，柔和侧光，木质桌面，极简构图。",
  },
  {
    id: "rh-img-3",
    source_kind: "image",
    status: "failed",
    created_at: revTs(2),
    source_thumbnail_url: "https://mock.local/reverse/src-3.png",
    summary: null, // 失败无结果 → 无 summary
    error_code: "REVERSE_FAILED",
    error_message: "图片解析失败，请换一张更清晰的图片重试"
  },
  {
    id: "rh-vid-1",
    source_kind: "video",
    status: "succeeded",
    created_at: revTs(3),
    source_thumbnail_url: null, // 🔴 BE 事实：video 源恒 null（不是忘了填）
    summary: "保温杯带货短片：产品特写 → 使用场景 → 卖点字幕 → 收尾定格。",
  },
  {
    id: "rh-vid-2",
    source_kind: "video",
    status: "running",
    created_at: revTs(4),
    source_thumbnail_url: null,
    summary: null, // 进行中无结果
  },
  {
    id: "rh-vid-3",
    source_kind: "video",
    status: "queued",
    created_at: revTs(5),
    source_thumbnail_url: null,
    summary: null,
  }
];
REVERSE_SEEDS.forEach((seed) => reverseJobs.set(seed.id, mkReverseJob(seed)));

/**
 * 🔴 FIX4 · **把「测试之间互不干扰」从排座位变成机制**。
 *
 * `reverseJobs` 是模块级、**可变、跨测试累积**的：软删标记、saved 状态、regenerate 打回的 queued 全都留着。
 * 上一版对此的处理是在测试文件里写一句「⚠️ 本 describe 会改 mock state → **必须放最后**」——
 * 那不是解法，那是**把顺序依赖写进注释、制度化了**。靠人记住座位表，下一条就会漏（FIX3 我刚认过这个病，
 * 紧挨着的下一条测试里就又犯了一次，还在注释里写明了「上一条刚把 rh-vid-1 打回 queued」）。
 *
 * ⚠️ 注意 vitest.setup.ts 的 `afterEach(() => server.resetHandlers())` **不管这个** ——
 * 它只重置 handler 覆盖，不碰模块级数据。名字听着像全清，实际不是（又一张假路标）。
 *
 * 调用方：反推相关测试文件的 `beforeEach`。**没有全局挂**，理由见回执：全局重置 = 变相给全仓开
 * shuffle，会掀出一堆既有的跨测试依赖，那是另一个包的活。
 */
export const resetReverseJobs = () => {
  reverseJobs.clear();
  reverseSeq = 0; // → 每条测试里 POST 出的 id 从 rp-1 / rpv-1 起，确定可预期
  REVERSE_SEEDS.forEach((seed) => reverseJobs.set(seed.id, mkReverseJob(seed)));
};

/**
 * 🔴 **唯一的读模型** —— 图片/视频、五种状态全走这一个函数（原先有三份：reverseJobRead 合成器 /
 * reverseVideoJobRead / reverseHistJobRead，三份各自决定「什么时候有 result」）。
 * 契约：queued/running 无 result；failed 无 result 但有 error_*；succeeded/saved 有 result；
 * video 的 result **内嵌** video_analysis。
 */
const reverseJobRead = (j: MockReverseJob) => ({
  id: j.id,
  status: j.status,
  source_kind: j.source_kind,
  source_asset_id: j.source_kind === "video" ? "video-asset-1" : "upload-1",
  target_format: "seedance_2_0",
  result:
    j.status === "succeeded" || j.status === "saved"
      ? j.source_kind === "video"
        ? { ...REVERSE_RESULT, video_analysis: REVERSE_VIDEO_ANALYSIS }
        : REVERSE_RESULT
      : null,
  error_code: j.error_code,
  error_message: j.error_message,
  provider: "apimart",
  model: "gemini-2.5-flash",
  prompt_tokens: j.source_kind === "video" ? 0 : 1200,
  completion_tokens: j.source_kind === "video" ? 0 : 480,
  credits: j.source_kind === "video" ? 6 : 0, // provider 引擎成本；租户固定 100 走 UsageRecord
  cost_cents: j.source_kind === "video" ? 0 : 3,
  created_at: j.created_at,
  updated_at: j.created_at,
  saved_at: j.saved_at
});

/** 列表项 = BE ReversePromptHistoryItem 的 6 字段（schemas:97-103，**无 result**）。 */
const reverseHistItem = (j: MockReverseJob) => ({
  id: j.id,
  source_kind: j.source_kind,
  status: j.status,
  created_at: j.created_at,
  source_thumbnail_url: j.source_thumbnail_url,
  summary: j.summary
});

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
// VIDEO-GEN-PARAMS-UI-0001：视频生成时长改自定义整数 4–15（原仅 5/10/15），校验内联在 video_gen 分支，故删本地枚举常量。
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
  if (badDuration(c.duration_sec)) return true; // FIX2（CB P1 · 机制）：批量 common.duration_sec 也是 int，小数非法（未加门即 red）
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
    tenant_name: `用户 ${i + 1}`, // 展示串「租户」→「用户」（key/字段名 tenant_name 不变）
    credits_used,
    cost_cents: 1500 + i * 137,
    task_count: success + failed,
    success_rate: Math.round((success / (success + failed)) * 1000) / 1000,
    balance: { total, used, reserved, remaining: Math.max(0, total - used - reserved) }
  };
});

// 平台全站消耗积分合计——既是 overview 平台方 total_credits_used，又是 VIP 缩放「我的占比」的分母（by-provider/timeseries）。
// 三处必须同源，故收拢为具名常量（改动需一致）。
const PLATFORM_TOTAL_CREDITS = 48213.5;

// VIP 客户（有 analytics_view 无 analytics_platform）只看**自己**的数据（BE 已按租户过滤）——mock 忠实返单租户，
// 不泄漏任何其它租户名/全站合计。前端另会隐藏「用户排行」（双保险）。
const OWN_TENANT = {
  tenant_id: "ten-mock",
  tenant_name: "华鼎（mock）",
  credits_used: 1280.5,
  cost_cents: 48200,
  task_count: 96,
  success_rate: 0.94,
  balance: { total: 1000, used: 120, reserved: 36, remaining: 844 }
};

// PROD-P0-ANALYTICS-TENANT-LEAK-UI-0001（FIX2 · 单一状态源）：整个 mock 的「我是谁 / 有什么权限 / analytics 能看什么」
// 收敛到唯一解析函数 resolveMockState()——所有消费方（/me 身份、mockPermissions、analyticsHandlers 的门禁与 scope、
// 品牌音色 doubao 兜底等）**一律走它**，不再各读各的 localStorage（FIX1 的「/me 走 registered、analytics 走旋钮」两套源已并线）。
// **绝不按 role 派生 entitlement**——自助注册 owner 在真 BE 也是 role=ADMIN（租户管理员 ≠ 平台超管），按 role 会让 free
// 新注册用户绕过门禁（线上事故：看到别租户数据 + 升级版卡不置灰）。
function readLS(key: string): string | null {
  try {
    return typeof localStorage !== "undefined" ? localStorage.getItem(key) : null;
  } catch {
    return null;
  }
}

// 真实注册链驱动的新注册身份（POST /register-tenant 成功后**只写这一个 key**；platform/plan/role 全由它推导，无需另写）。
interface RegisteredIdentity {
  tenant: { id: string; slug: string; name: string };
  user: { id: string; tenant_id: string; email: string; full_name: string | null; role: "admin" };
}
function readRegistered(): RegisteredIdentity | null {
  const raw = readLS("hd_mock_registered");
  if (!raw) return null;
  try {
    return JSON.parse(raw) as RegisteredIdentity;
  } catch {
    return null;
  }
}

interface MockState {
  isPlatform: boolean;
  plan: "huading" | "free";
  role: "admin" | "creator";
  identity: RegisteredIdentity | null;
}
// 唯一状态源。走过真实注册链（有 hd_mock_registered）→ 以注册身份为权威：非平台 + free + role admin（正是线上那个坑，
// self-serve owner=ADMIN 保留）。否则回落三旋钮（默认平台租户「华鼎AI」→ 全权限，保后台/联调零回归）：
//   hd_mock_platform: 平台租户？默认 "1"；"0" → 普通租户 · hd_mock_plan: "huading"（默认）| "free" · hd_mock_role: "admin"（默认）| "creator"
function resolveMockState(): MockState {
  const identity = readRegistered();
  if (identity) {
    return { isPlatform: false, plan: "free", role: "admin", identity };
  }
  return {
    isPlatform: readLS("hd_mock_platform") !== "0",
    plan: readLS("hd_mock_plan") === "free" ? "free" : "huading",
    role: readLS("hd_mock_role") === "creator" ? "creator" : "admin",
    identity: null
  };
}
// huading 通路 entitlement——逐字镜像已上线 BE plan_access.py「平台租户 OR huading 订阅」（**不含 role**）。
function mockHuadingAccess(state: MockState): boolean {
  return state.isPlatform || state.plan === "huading";
}
// 角色基础权限：逐字镜像 BE deps.py::_ROLE_PERMISSIONS（真 BE 无 "video:read"）。entitlement 权限不写死进角色。
const ROLE_PERMISSIONS: Record<"admin" | "creator", string[]> = {
  admin: ["tenant:admin", "content:operate", "video:create", "video:review", "dev:access"],
  creator: ["video:create"]
};
// /auth/me.permissions = sorted(角色权限 ∪ {voice_clone_vip, analytics_view} if huadingAccess
// ∪ {analytics_platform, admin_console} if isPlatform)。admin_console 与 analytics_platform **同源派生**
// （= 平台租户，ADMIN-CONSOLE-UI-0001），不凭空塞、不引入第二套旋钮。
function mockPermissions(state: MockState): string[] {
  const perms = new Set(ROLE_PERMISSIONS[state.role]);
  if (mockHuadingAccess(state)) {
    perms.add("voice_clone_vip");
    perms.add("analytics_view");
  }
  if (state.isPlatform) {
    perms.add("analytics_platform");
    perms.add("admin_console");
  }
  return [...perms].sort();
}

function analyticsHandlers() {
  const A = `${BASE}/api/v1/admin/analytics`;
  // 门禁与 scope 走**单一状态源** resolveMockState()（与 /me entitlement 同源，不再直读旋钮）。
  // 无 analytics_view（= 非平台租户且非 huading）→ 403 ANALYTICS_PLAN_REQUIRED；平台方 → 全站 46 租户，VIP 客户 → 只本租户。
  const guard = () =>
    mockHuadingAccess(resolveMockState()) ? null : err(403, "ANALYTICS_PLAN_REQUIRED", "Analytics requires the huading plan.");
  const scopedTenants = () => (resolveMockState().isPlatform ? ANALYTICS_TENANTS : [OWN_TENANT]);
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
    return [...scopedTenants()].sort((a, b) => (fn(a) - fn(b)) * dir);
  };
  return [
    http.get(`${A}/overview`, ({ request }) => {
      const g = guard();
      if (g) return g;
      const url = new URL(request.url);
      const platform = resolveMockState().isPlatform;
      const ownSuccess = Math.round(OWN_TENANT.task_count * OWN_TENANT.success_rate);
      // 全站合计（平台）vs 我的合计（VIP）——VIP 绝不返全站数字（那也是一种泄漏）。
      return ok({
        total_credits_used: platform ? PLATFORM_TOTAL_CREDITS : OWN_TENANT.credits_used,
        total_cost_cents: platform ? 1892340 : OWN_TENANT.cost_cents,
        task_count: platform ? 5230 : OWN_TENANT.task_count,
        success_count: platform ? 4890 : ownSuccess,
        failed_count: platform ? 340 : OWN_TENANT.task_count - ownSuccess,
        tenant_count: platform ? ANALYTICS_TENANTS.length : 1,
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
      return ok({ items: sorted.slice(offset, offset + limit), total: scopedTenants().length });
    }),
    http.get(`${A}/by-provider`, () => {
      const g = guard();
      if (g) return g;
      // VIP（非平台）→ 按自己占比缩放为「我的」拆分，不返全站聚合（share_pct 是占比，不随缩放变）。
      const s = resolveMockState().isPlatform ? 1 : OWN_TENANT.credits_used / PLATFORM_TOTAL_CREDITS;
      const scaleRow = (r: { provider: string; model: string | null; credits_used: number; cost_cents: number; task_count: number; share_pct: number }) => ({
        ...r,
        credits_used: Math.round(r.credits_used * s * 10) / 10,
        cost_cents: Math.round(r.cost_cents * s),
        task_count: Math.round(r.task_count * s)
      });
      return ok({
        items: [
          { provider: "seedance", model: "i2v-v1", credits_used: 21500, cost_cents: 812000, task_count: 2100, share_pct: 44.6 },
          { provider: "video_gen", model: "vg-pro", credits_used: 15200, cost_cents: 540300, task_count: 1630, share_pct: 31.5 },
          { provider: "copywriting", model: null, credits_used: 6800, cost_cents: 210400, task_count: 980, share_pct: 14.1 },
          { provider: "image", model: "flux-1", credits_used: 4713.5, cost_cents: 329640, task_count: 520, share_pct: 9.8 }
        ].map(scaleRow)
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
      // VIP（非平台）→ 趋势缩放为「我的」用量，不返全站曲线。
      const s = resolveMockState().isPlatform ? 1 : OWN_TENANT.credits_used / PLATFORM_TOTAL_CREDITS;
      const buckets = Array.from({ length: count }, (_, i) => {
        const d = new Date(base);
        d.setDate(d.getDate() + i * step);
        const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
        return {
          date: iso,
          credits_used: Math.round((800 + Math.sin(i) * 300 + i * 40) * s * 10) / 10,
          cost_cents: Math.round((30000 + i * 4200 + (i % 3) * 1500) * s),
          task_count: Math.round((120 + i * 9 + (i % 4) * 15) * s)
        };
      });
      return ok({ buckets });
    })
  ];
}

// ── 管理员后台 (ADMIN-CONSOLE-UI-0001 · FIX1 已按真实 BE #165 逐字段对齐) mock ──
// 契约源：backend/app/schemas/admin_console.py + routes/admin_console.py（merge 618d7b94）。
// 前缀 /api/v1/admin/console，统一 require_platform_admin；分页 page/page_size；门禁走 resolveMockState() 单一源。
// 写操作全部落审计（内存态，页面即时可见）；错误 message 为中文、UI 原样展示。
interface MockSubscription { id: string; total: number; used: number; reserved: number; remaining: number }
interface MockAdminTenant {
  tenant_id: string; slug: string; name: string; status: "active" | "suspended" | "closed";
  created_at: string; owner_email: string | null; plan_code: "free" | "basic" | "huading" | null;
  subscription: MockSubscription | null; task_count: number; is_platform: boolean;
}
// 🔴 种子是**工厂**，不是常量 —— 每次调用现造一份全新嵌套字面量。
// 理由（这里踩过就回不来）：subscription 是**就地改写**的（credits handler 直接 `sub.total = newTotal`）。
// 若照「常量种子 + 浅拷贝」写，两次 reset 会共用同一个 subscription 对象 —— 看着重置了，其实没有。
// 工厂让「重置后 = 首次加载」在结构上成立，不靠拷贝深度是否够这种需要人去核对的前提。
const adminTenantSeeds = (): MockAdminTenant[] => [
  { tenant_id: "ten-mock", slug: "huading", name: "华鼎（mock）", status: "active", created_at: "2026-01-01T08:00:00Z", owner_email: "qa@huading.test", plan_code: "huading", subscription: { id: "sub-mock", total: 1000, used: 120, reserved: 36, remaining: 844 }, task_count: 96, is_platform: true },
  { tenant_id: "ten-acme", slug: "acme", name: "Acme 电商", status: "active", created_at: "2026-06-02T09:30:00Z", owner_email: "owner@acme.test", plan_code: "huading", subscription: { id: "sub-acme", total: 20000, used: 5000, reserved: 1000, remaining: 14000 }, task_count: 42, is_platform: false },
  { tenant_id: "ten-beta", slug: "beta", name: "贝塔传媒", status: "active", created_at: "2026-07-01T14:00:00Z", owner_email: "ops@beta.test", plan_code: null, subscription: null, task_count: 3, is_platform: false },
  { tenant_id: "ten-gamma", slug: "gamma", name: "伽马食品", status: "suspended", created_at: "2026-05-20T11:00:00Z", owner_email: "boss@gamma.test", plan_code: "basic", subscription: { id: "sub-gamma", total: 5000, used: 4200, reserved: 600, remaining: 200 }, task_count: 17, is_platform: false }
];
const adminTenants = new Map<string, MockAdminTenant>();
interface MockAdminTask {
  id: string; task_family: "video" | "reverse_prompt" | "ecom_replicate"; tenant_id: string; tenant_slug: string; tenant_name: string;
  mode: string; label: string | null; video_mode: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled"; progress: number | null;
  error_code: string | null; error_message: string | null;
  created_at: string; started_at: string | null; finished_at: string | null; duration_seconds: number | null; retryable: boolean;
}
// 同上，工厂：retry handler 就地改写 status/progress/retryable。
const adminTaskSeeds = (): MockAdminTask[] => [
  { id: "job-f1", task_family: "video", tenant_id: "ten-acme", tenant_slug: "acme", tenant_name: "Acme 电商", mode: "avatar", label: "口播视频", video_mode: "avatar_talk", status: "failed", progress: 35, error_code: "PROVIDER_TIMEOUT", error_message: "上游生成超时，已释放预留额度", created_at: "2026-07-12T10:00:00Z", started_at: "2026-07-12T10:01:00Z", finished_at: "2026-07-12T10:06:00Z", duration_seconds: 300, retryable: true },
  { id: "job-f2", task_family: "ecom_replicate", tenant_id: "ten-gamma", tenant_slug: "gamma", tenant_name: "伽马食品", mode: "ecom_replicate", label: "详情图复刻", video_mode: null, status: "failed", progress: 0, error_code: "tenant_quota_exceeded", error_message: "额度不足，任务未启动", created_at: "2026-07-12T11:00:00Z", started_at: null, finished_at: null, duration_seconds: null, retryable: true },
  { id: "job-f3", task_family: "reverse_prompt", tenant_id: "ten-beta", tenant_slug: "beta", tenant_name: "贝塔传媒", mode: "reverse_prompt", label: "视频反推", video_mode: null, status: "failed", progress: 10, error_code: "PROVIDER_ERROR", error_message: "上游分析失败，预留已释放", created_at: "2026-07-12T13:00:00Z", started_at: "2026-07-12T13:01:00Z", finished_at: "2026-07-12T13:02:00Z", duration_seconds: 60, retryable: true },
  { id: "job-r1", task_family: "video", tenant_id: "ten-acme", tenant_slug: "acme", tenant_name: "Acme 电商", mode: "avatar", label: "口播视频", video_mode: "avatar_talk", status: "running", progress: 60, error_code: null, error_message: null, created_at: "2026-07-13T08:00:00Z", started_at: "2026-07-13T08:01:00Z", finished_at: null, duration_seconds: null, retryable: false },
  { id: "job-d1", task_family: "video", tenant_id: "ten-beta", tenant_slug: "beta", tenant_name: "贝塔传媒", mode: "avatar", label: "电商带货", video_mode: "seedance_i2v", status: "succeeded", progress: 100, error_code: null, error_message: null, created_at: "2026-07-11T09:00:00Z", started_at: "2026-07-11T09:00:30Z", finished_at: "2026-07-11T09:02:00Z", duration_seconds: 90, retryable: false },
  { id: "job-q1", task_family: "video", tenant_id: "ten-acme", tenant_slug: "acme", tenant_name: "Acme 电商", mode: "avatar", label: "口播视频", video_mode: "avatar_talk", status: "queued", progress: 0, error_code: null, error_message: null, created_at: "2026-07-13T09:00:00Z", started_at: null, finished_at: null, duration_seconds: null, retryable: false }
];
const adminTasks = new Map<string, MockAdminTask>();
// 用量流水（6 条，> mock 导出上限 3 → 无筛选导出走 422；按租户筛后 ≤3 → 200 CSV。字段逐字 BE AdminUsageItem）。
const ADMIN_USAGE = [
  { id: "u-1", created_at: "2026-07-10T10:00:00Z", tenant_id: "ten-acme", tenant_slug: "acme", tenant_name: "Acme 电商", capability: "video_generate", provider: "seedance", model: "i2v-v1", quantity: 1, unit: "视频", credits: 300, cost_cents: 4200, status: "settled", video_task_id: "job-f1" },
  { id: "u-2", created_at: "2026-07-10T12:00:00Z", tenant_id: "ten-acme", tenant_slug: "acme", tenant_name: "Acme 电商", capability: "image_generate", provider: "apimart", model: "flux-1", quantity: 5, unit: "张", credits: 75, cost_cents: 900, status: "settled", video_task_id: null },
  { id: "u-3", created_at: "2026-07-11T09:02:00Z", tenant_id: "ten-beta", tenant_slug: "beta", tenant_name: "贝塔传媒", capability: "copywriting", provider: "deepseek", model: "v3", quantity: 1, unit: "篇", credits: 10, cost_cents: 30, status: "settled", video_task_id: "job-d1" },
  { id: "u-4", created_at: "2026-07-12T10:06:00Z", tenant_id: "ten-acme", tenant_slug: "acme", tenant_name: "Acme 电商", capability: "video_generate", provider: "seedance", model: "i2v-v1", quantity: 1, unit: "视频", credits: 300, cost_cents: 0, status: "released", video_task_id: "job-f1" },
  { id: "u-5", created_at: "2026-07-12T15:00:00Z", tenant_id: "ten-gamma", tenant_slug: "gamma", tenant_name: "伽马食品", capability: "voice_clone", provider: "doubao", model: null, quantity: 1, unit: "音色", credits: 30000, cost_cents: 990000, status: "settled", video_task_id: null },
  { id: "u-6", created_at: "2026-07-13T08:01:00Z", tenant_id: "ten-acme", tenant_slug: "acme", tenant_name: "Acme 电商", capability: "video_generate", provider: "seedance", model: "i2v-v1", quantity: 1, unit: "视频", credits: 300, cost_cents: 4200, status: "reserved", video_task_id: "job-r1" }
] as const;
const EXPORT_MAX_ROWS = 3; // 真 BE 上限 50000；mock 缩小使 422 与 200 两路径都可测（message 格式逐字镜像 BE）
// 音色槽位：**合一列表**（BE AdminVoiceSlotItem，scope 区分平台池/租户专属）。remaining = 全列表未占用数（镜像 BE）。
interface MockVoiceSlot {
  speaker_id: string; scope: "platform" | "tenant"; sources: string[];
  tenant_id: string | null; tenant_slug: string | null; tenant_name: string | null;
  occupied: boolean; brand_voice_id: string | null; brand_voice_name: string | null; brand_voice_status: string | null;
}
// 同上，工厂：分配 handler 往这个数组 push 新槽位。
const voiceSlotSeeds = (): MockVoiceSlot[] => [
  { speaker_id: "S_pool_001", scope: "platform", sources: ["env"], tenant_id: "ten-acme", tenant_slug: "acme", tenant_name: "Acme 电商", occupied: true, brand_voice_id: "bv-ready-1", brand_voice_name: "我的主播音", brand_voice_status: "ready" },
  { speaker_id: "S_pool_002", scope: "platform", sources: ["env"], tenant_id: null, tenant_slug: null, tenant_name: null, occupied: false, brand_voice_id: null, brand_voice_name: null, brand_voice_status: null },
  { speaker_id: "S_pool_003", scope: "platform", sources: ["env"], tenant_id: null, tenant_slug: null, tenant_name: null, occupied: false, brand_voice_id: null, brand_voice_name: null, brand_voice_status: null },
  { speaker_id: "S_acme_001", scope: "tenant", sources: ["tenant_config"], tenant_id: "ten-acme", tenant_slug: "acme", tenant_name: "Acme 电商", occupied: true, brand_voice_id: "bv-ready-1", brand_voice_name: "我的主播音", brand_voice_status: "ready" }
];
const voiceSlots: MockVoiceSlot[] = [];
// 审计日志（只写不改不删；字段逐字 BE AdminAuditLogItem）。时间戳用序号合成（确定性）。
let auditSeq = 1;
interface MockAuditRow {
  id: string; actor_user_id: string; actor_email: string | null; actor_tenant_id: string; action: string;
  target_tenant_id: string | null; target_tenant_slug: string | null; target_id: string | null;
  before: Record<string, unknown> | null; after: Record<string, unknown> | null; reason: string | null; created_at: string;
}
const adminAuditSeeds = (): MockAuditRow[] => [
  { id: "audit-0", actor_user_id: "u-mock", actor_email: "qa@huading.test", actor_tenant_id: "ten-mock", action: "voice_slot_assign", target_tenant_id: "ten-acme", target_tenant_slug: "acme", target_id: null, before: { speaker_ids: [] }, after: { speaker_ids: ["S_acme_001"] }, reason: "首批客户开通", created_at: "2026-07-13T09:00:00Z" }
];
const adminAudit: MockAuditRow[] = [];

/**
 * 🔴 **admin 后台 mock store 重置** —— 与反推域的 `resetReverseJobs()` 同构（PR #183 FIX4 的先例）：
 * 每条测试从**同一份 seed** 出发，顺序依赖在结构上不可能存在。
 *
 * 这里原先的处置是在 admin-console.test.ts 顶上写「只读用例在前、写操作在后（顺序即契约）」，并把
 * 「顺序即契约」写进了 describe 的名字 —— **那不是契约，是把 bug 命名成了 feature**。顺序从来不是契约，
 * 它只是状态泄漏的补偿动作；`--sequence.shuffle` 一开就散（多 seed 实测：同一份代码，红 1~6 条不等）。
 *
 * ⚠️ 同 `resetReverseJobs()` 的告警：vitest.setup.ts 的 `afterEach(() => server.resetHandlers())` **不管这个** ——
 * 它只重置 handler 覆盖、不碰模块级数据。名字听着像全清，实际不是。
 *
 * 覆盖 admin 段全部 5 个可变模块级状态：adminTenants / adminTasks / voiceSlots / adminAudit / auditSeq。
 * （`ADMIN_USAGE` 不在此列，真依据是**它没有写路径** —— 全部消费方只 `filter` / `spread`（`{ ...r }`），
 *  没有一处改它的元素或长度，故跨测试不会泄漏、无需重置。注意 `as const` **只是 TypeScript 编译期只读，
 *  不是运行时冻结**（`Object.freeze` 才是）；真正兜住的是「无写路径」这个事实，不是 `as const`。FIX1 · Codex B。）
 *
 * 调用方：碰 admin MSW 态的测试文件的 `beforeEach`。**没有全局挂**（沿用反推域的判断：全局重置 =
 * 变相给全仓开 shuffle，会掀出别的域的既有依赖，那是另一个包的活）。
 */
export const resetAdminConsole = () => {
  adminTenants.clear();
  adminTenantSeeds().forEach((t) => adminTenants.set(t.tenant_id, t));
  adminTasks.clear();
  adminTaskSeeds().forEach((t) => adminTasks.set(t.id, t));
  voiceSlots.length = 0;
  voiceSlots.push(...voiceSlotSeeds());
  adminAudit.length = 0;
  adminAudit.push(...adminAuditSeeds());
  auditSeq = 1; // → 每条测试里写出的审计 id / created_at 从同一起点递增，确定可预期
};
resetAdminConsole();
function pushAudit(action: string, target: MockAdminTenant, targetId: string | null, before: Record<string, unknown> | null, after: Record<string, unknown> | null, reason: string | null) {
  auditSeq += 1;
  adminAudit.unshift({
    id: `audit-${auditSeq}`,
    actor_user_id: "u-mock", actor_email: "qa@huading.test", actor_tenant_id: "ten-mock",
    action, target_tenant_id: target.tenant_id, target_tenant_slug: target.slug, target_id: targetId,
    before, after, reason,
    created_at: `2026-07-13T10:${String(auditSeq).padStart(2, "0")}:00Z`
  });
}
const tenantItem = (t: MockAdminTenant) => ({
  tenant_id: t.tenant_id, slug: t.slug, name: t.name, status: t.status, created_at: t.created_at,
  owner_email: t.owner_email, plan_code: t.plan_code, subscription: t.subscription ? { ...t.subscription } : null, task_count: t.task_count
});
function adminConsoleHandlers() {
  const C = `${BASE}/api/v1/admin/console`;
  // 统一平台门禁（镜像 BE require_platform_admin）——与 /me 的 admin_console 同源（resolveMockState）。
  const guard = () =>
    resolveMockState().isPlatform ? null : err(403, "PLATFORM_ADMIN_REQUIRED", "Platform administrator access is required.");
  // 分页：page/page_size（镜像 BE PageQuery ge=1 / PageSizeQuery le=100），响应含 page/page_size。
  const paginate = <T,>(rows: T[], url: URL) => {
    const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
    const pageSize = Math.min(100, Math.max(1, Number(url.searchParams.get("page_size") ?? 20)));
    return { items: rows.slice((page - 1) * pageSize, page * pageSize), total: rows.length, page, page_size: pageSize };
  };
  // 镜像 BE _date_bounds：from > to → 422 INVALID_DATE_RANGE（usage / export / tasks 共用）。
  const dateRangeErr = (url: URL) => {
    const from = url.searchParams.get("from") ?? "";
    const to = url.searchParams.get("to") ?? "";
    return from && to && from > to ? err(422, "INVALID_DATE_RANGE", "开始日期不能晚于结束日期。") : null;
  };
  const filterUsage = (url: URL) => {
    const p = (k: string) => url.searchParams.get(k) ?? "";
    return ADMIN_USAGE.filter((r) => {
      if (p("tenant_id") && r.tenant_id !== p("tenant_id")) return false;
      if (p("capability") && r.capability !== p("capability")) return false;
      if (p("provider") && r.provider !== p("provider")) return false;
      if (p("status") && r.status !== p("status")) return false;
      if (p("from") && r.created_at.slice(0, 10) < p("from")) return false;
      if (p("to") && r.created_at.slice(0, 10) > p("to")) return false;
      return true;
    });
  };
  const activeSubOr422 = (t: MockAdminTenant) =>
    t.subscription ? null : err(404, "ACTIVE_SUBSCRIPTION_NOT_FOUND", "Tenant has no active subscription.");
  return [
    http.get(`${C}/tenants`, ({ request }) => {
      const g = guard();
      if (g) return g;
      const url = new URL(request.url);
      const q = (url.searchParams.get("q") ?? "").toLowerCase();
      const plan = url.searchParams.get("plan") ?? "";
      const status = url.searchParams.get("status") ?? "";
      const sort = url.searchParams.get("sort") ?? "created_at";
      const order = url.searchParams.get("order") ?? "desc";
      let rows = [...adminTenants.values()];
      if (q) rows = rows.filter((t) => t.slug.includes(q) || t.name.toLowerCase().includes(q) || (t.owner_email ?? "").toLowerCase().includes(q));
      if (plan) rows = rows.filter((t) => t.plan_code === plan);
      if (status) rows = rows.filter((t) => t.status === status);
      const key: Record<string, (t: MockAdminTenant) => number | string> = {
        created_at: (t) => t.created_at,
        credits_used: (t) => t.subscription?.used ?? 0,
        balance: (t) => t.subscription?.remaining ?? 0
      };
      const fn = key[sort] ?? key.created_at;
      const dir = order === "asc" ? 1 : -1;
      rows.sort((a, b) => (fn(a) < fn(b) ? -1 : fn(a) > fn(b) ? 1 : 0) * dir);
      return ok(paginate(rows.map(tenantItem), url));
    }),
    http.get(`${C}/tenants/:id`, ({ params }) => {
      const g = guard();
      if (g) return g;
      const t = adminTenants.get(params.id as string);
      if (!t) return err(404, "TENANT_NOT_FOUND", "Tenant not found.");
      return ok({
        tenant: tenantItem(t),
        recent_tasks: [...adminTasks.values()].filter((x) => x.tenant_id === t.tenant_id).sort((a, b) => (a.created_at < b.created_at ? 1 : -1)).slice(0, 10).map((x) => ({ ...x })),
        recent_usage: [...ADMIN_USAGE].filter((u) => u.tenant_id === t.tenant_id).sort((a, b) => (a.created_at < b.created_at ? 1 : -1)).slice(0, 10).map((u) => ({ ...u })),
        voice_slots: voiceSlots.filter((s) => s.tenant_id === t.tenant_id).map((s) => ({ ...s }))
      });
    }),
    // 余额增减：锁订阅 + 下限保护（镜像 BE adjust_credits）；审计 before/after = 订阅快照全量 dump。
    http.post(`${C}/tenants/:id/credits`, async ({ params, request }) => {
      const g = guard();
      if (g) return g;
      const t = adminTenants.get(params.id as string);
      if (!t) return err(404, "TENANT_NOT_FOUND", "Tenant not found.");
      const body = (await request.json()) as { delta?: number; reason?: string };
      if (typeof body.delta !== "number" || !Number.isFinite(body.delta) || body.delta === 0)
        return err(422, "VALIDATION_ERROR", "额度调整值不能为 0");
      if (!body.reason?.trim()) return err(422, "VALIDATION_ERROR", "请填写额度调整理由");
      if (body.reason.length > 500) return err(422, "VALIDATION_ERROR", "理由长度需在 1–500 字以内");
      const noSub = activeSubOr422(t);
      if (noSub) return noSub;
      const sub = t.subscription as MockSubscription;
      const before = { ...sub };
      const newTotal = sub.total + body.delta;
      if (newTotal < sub.used + sub.reserved)
        return err(422, "CREDIT_TOTAL_BELOW_COMMITTED", "扣减后额度会低于已用+预留，无法执行。");
      sub.total = newTotal;
      sub.remaining = newTotal - sub.used - sub.reserved;
      pushAudit("credits_adjust", t, sub.id, { ...before }, { ...sub }, body.reason.trim());
      return ok({ tenant_id: t.tenant_id, delta: body.delta, subscription: { ...sub } });
    }),
    // 改套餐（PATCH，镜像 BE change_plan）：平台租户降级 → 422；审计 before/after 含 plan_code + subscription。
    http.patch(`${C}/tenants/:id/plan`, async ({ params, request }) => {
      const g = guard();
      if (g) return g;
      const t = adminTenants.get(params.id as string);
      if (!t) return err(404, "TENANT_NOT_FOUND", "Tenant not found.");
      const body = (await request.json()) as { plan_code?: string; reason?: string };
      if (!["free", "basic", "huading"].includes(body.plan_code ?? "")) return err(422, "VALIDATION_ERROR", "plan_code 非法");
      if ((body.reason ?? "").length > 500) return err(422, "VALIDATION_ERROR", "理由长度需在 500 字以内");
      if (t.is_platform && body.plan_code !== "huading")
        return err(422, "CANNOT_DOWNGRADE_PLATFORM_TENANT", "平台租户不能降级自身套餐。");
      const noSub = activeSubOr422(t);
      if (noSub) return noSub;
      const sub = t.subscription as MockSubscription;
      const before = { plan_code: t.plan_code, subscription: { ...sub } };
      t.plan_code = body.plan_code as MockAdminTenant["plan_code"];
      pushAudit("plan_change", t, sub.id, before, { plan_code: t.plan_code, subscription: { ...sub } }, body.reason ?? null);
      return ok({ tenant_id: t.tenant_id, plan_code: t.plan_code, subscription: { ...sub } });
    }),
    // 启用/停用（PATCH {active:bool} → status active/suspended）：停用平台租户 → 422（纵深防御，前端也置灰）。
    http.patch(`${C}/tenants/:id/status`, async ({ params, request }) => {
      const g = guard();
      if (g) return g;
      const t = adminTenants.get(params.id as string);
      if (!t) return err(404, "TENANT_NOT_FOUND", "Tenant not found.");
      const body = (await request.json()) as { active?: boolean; reason?: string };
      if (typeof body.active !== "boolean") return err(422, "VALIDATION_ERROR", "active 必须为布尔值");
      if ((body.reason ?? "").length > 500) return err(422, "VALIDATION_ERROR", "理由长度需在 500 字以内");
      if (t.is_platform && !body.active) return err(422, "CANNOT_SUSPEND_PLATFORM_TENANT", "平台租户不能停用自身账号。");
      const before = { status: t.status };
      t.status = body.active ? "active" : "suspended";
      pushAudit("status_change", t, null, before, { status: t.status }, body.reason ?? null);
      return ok({ tenant_id: t.tenant_id, status: t.status });
    }),
    // 音色槽位总览：合一列表 + remaining（**全列表**未占用数，镜像 BE sum(not occupied)）。
    http.get(`${C}/voice-slots`, () => {
      const g = guard();
      if (g) return g;
      const items = voiceSlots.map((s) => ({ ...s }));
      return ok({ items, total: items.length, remaining: items.filter((s) => !s.occupied).length });
    }),
    // 分配专属槽位（POST /tenants/{id}/voice-slots，幂等：重复挂 changed:false）；审计 speaker_ids 前→后。
    // 🔴 全局唯一（P1-1 镜像 BE voice_slots.py 冲突检查）：speaker_id 已属于平台池或任何**其它**租户 →
    // 422 VOICE_SLOT_ASSIGNMENT_FAILED（message 逐字同 BE）。只有全局空闲（或本就属于该租户=幂等）才 200。
    http.post(`${C}/tenants/:id/voice-slots`, async ({ params, request }) => {
      const g = guard();
      if (g) return g;
      const t = adminTenants.get(params.id as string);
      if (!t) return err(404, "TENANT_NOT_FOUND", "Tenant not found.");
      const body = (await request.json()) as { speaker_id?: string; reason?: string };
      if (!/^S_[A-Za-z0-9_-]{1,157}$/.test(body.speaker_id ?? "")) return err(422, "VALIDATION_ERROR", "speaker_id 格式不正确");
      if ((body.reason ?? "").length > 500) return err(422, "VALIDATION_ERROR", "理由长度需在 500 字以内");
      const conflict = voiceSlots.some(
        (s) => s.speaker_id === body.speaker_id && (s.scope === "platform" || s.tenant_id !== t.tenant_id)
      );
      if (conflict)
        return err(422, "VOICE_SLOT_ASSIGNMENT_FAILED", "Speaker ID is already assigned to another tenant or the platform pool.");
      const mine = () => voiceSlots.filter((s) => s.scope === "tenant" && s.tenant_id === t.tenant_id).map((s) => s.speaker_id);
      const beforeIds = mine();
      const changed = !beforeIds.includes(body.speaker_id as string);
      if (changed) {
        voiceSlots.push({ speaker_id: body.speaker_id as string, scope: "tenant", sources: ["tenant_config"], tenant_id: t.tenant_id, tenant_slug: t.slug, tenant_name: t.name, occupied: false, brand_voice_id: null, brand_voice_name: null, brand_voice_status: null });
      }
      // 镜像 BE record_audit：**无条件**落审计（幂等重复分配也记一条，changed:false）。
      pushAudit("voice_slot_assign", t, t.tenant_id, { speaker_ids: beforeIds }, { speaker_id: body.speaker_id as string, changed, speaker_ids: mine() }, body.reason ?? null);
      return ok({ tenant_id: t.tenant_id, speaker_id: body.speaker_id as string, changed, speaker_ids: mine() });
    }),
    http.get(`${C}/usage`, ({ request }) => {
      const g = guard();
      if (g) return g;
      const url = new URL(request.url);
      const bad = dateRangeErr(url);
      if (bad) return bad;
      return ok(paginate(filterUsage(url).map((r) => ({ ...r })), url));
    }),
    // CSV 导出：BOM + 13 列 header 逐字镜像 BE；超上限 → 422 USAGE_EXPORT_TOO_LARGE（中文原样展示）。
    http.get(`${C}/usage/export`, ({ request }) => {
      const g = guard();
      if (g) return g;
      const badExp = dateRangeErr(new URL(request.url));
      if (badExp) return badExp;
      const rows = filterUsage(new URL(request.url));
      if (rows.length > EXPORT_MAX_ROWS)
        return err(422, "USAGE_EXPORT_TOO_LARGE", `导出记录超过 ${EXPORT_MAX_ROWS} 条，请缩小时间范围。`);
      const head = "created_at,tenant_id,tenant_slug,tenant_name,capability,provider,model,quantity,unit,credits,cost_cents,status,video_task_id";
      const lines = rows.map((r) => [r.created_at, r.tenant_id, r.tenant_slug, r.tenant_name, r.capability, r.provider, r.model ?? "", r.quantity, r.unit, r.credits, r.cost_cents, r.status, r.video_task_id ?? ""].join(","));
      return new HttpResponse("﻿" + [head, ...lines].join("\r\n"), {
        status: 200,
        headers: { "Content-Type": "text/csv", "Content-Disposition": 'attachment; filename="usage-records.csv"' }
      });
    }),
    // 任务监控：筛选 status（done 归一 succeeded，镜像 BE）/ task_family / tenant_id / from / to。
    http.get(`${C}/tasks`, ({ request }) => {
      const g = guard();
      if (g) return g;
      const url = new URL(request.url);
      const badTasks = dateRangeErr(url);
      if (badTasks) return badTasks;
      const p = (k: string) => url.searchParams.get(k) ?? "";
      let rows = [...adminTasks.values()];
      const st = p("status") === "done" ? "succeeded" : p("status");
      if (st) rows = rows.filter((r) => r.status === st);
      if (p("task_family")) rows = rows.filter((r) => r.task_family === p("task_family"));
      if (p("tenant_id")) rows = rows.filter((r) => r.tenant_id === p("tenant_id"));
      if (p("from")) rows = rows.filter((r) => r.created_at.slice(0, 10) >= p("from"));
      if (p("to")) rows = rows.filter((r) => r.created_at.slice(0, 10) <= p("to"));
      rows.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
      return ok(paginate(rows.map((r) => ({ ...r })), url));
    }),
    // 重跑（202）：回执三态（FIX1 冻结，**无 estimate_basis**）。按任务类型（镜像 BE，字段名 task_family 不变）：
    //   job-f1 released avatar_talk → charged:true, credits=原预留 1501, is_estimate:true（唯一 estimate）
    //   job-f3 released 反推固定价  → charged:true, credits=100, is_estimate:false
    //   job-f2 电商复刻（确认已扣） → charged:false, credits=0, is_estimate:false
    http.post(`${C}/tasks/:id/retry`, ({ params }) => {
      const g = guard();
      if (g) return g;
      const t = adminTasks.get(params.id as string);
      if (!t) return err(404, "TASK_NOT_FOUND", "Task not found.");
      if (!t.retryable) return err(409, "TASK_NOT_RETRYABLE", "Only failed tasks can be retried.");
      const RETRY_RECEIPT: Record<string, { charged: boolean; credits: number; is_estimate: boolean }> = {
        "job-f1": { charged: true, credits: 1501, is_estimate: true },
        "job-f3": { charged: true, credits: 100, is_estimate: false },
        "job-f2": { charged: false, credits: 0, is_estimate: false }
      };
      const receipt = RETRY_RECEIPT[t.id] ?? { charged: false, credits: 0, is_estimate: false };
      const before = { status: t.status, progress: t.progress };
      t.status = "queued";
      t.progress = 0;
      t.retryable = false;
      pushAudit("task_retry", adminTenants.get(t.tenant_id) as MockAdminTenant, t.id, before, { status: "queued", progress: 0 }, null);
      return HttpResponse.json(
        { data: { id: t.id, task_family: t.task_family, tenant_id: t.tenant_id, status: "queued", progress: 0, ...receipt }, error: null, request_id: "mock-req" },
        { status: 202 }
      );
    }),
    // 审计日志（GET /audit-logs——query 仅 action / target_tenant_id / 分页；只读）。
    http.get(`${C}/audit-logs`, ({ request }) => {
      const g = guard();
      if (g) return g;
      const url = new URL(request.url);
      const p = (k: string) => url.searchParams.get(k) ?? "";
      let rows = [...adminAudit];
      if (p("action")) rows = rows.filter((r) => r.action === p("action"));
      if (p("target_tenant_id")) rows = rows.filter((r) => r.target_tenant_id === p("target_tenant_id"));
      return ok(paginate(rows.map((r) => ({ ...r })), url));
    })
  ];
}

// ── 电商详情图·强制复刻 (ECOM-REPLICATE-UI-0001 · FIX1) mock ── 镜像真实 BE PR #140 + FIX2 GET 契约：
// POST /replicate(201, plan_ready, plan.outputs 全 planned, 不扣) → confirm(202, minimal, generating, 幂等一次扣) →
// GET /replicate/{id}(轮询推进逐张 succeeded 带 actual_w/h + download_url) → outputs/{index}/retry(202, 单张, 不重复扣)。
const ECOM_REPLICATE_RATE = 15; // credit/张（= BE engine_ecom_replicate_credits_per_image；前端从 total_credits 取，不硬编码）
// BE theme 为机器枚举键（backend services/ecom_replicate.py _MAIN_THEMES/_DETAIL_THEMES）——mock 忠实返原键，UI 本地化。
const ECOM_MAIN_THEMES = ["layout_match", "color_match", "campaign_match", "social_match", "white_background"];
const ECOM_DETAIL_THEMES = ["hero", "material", "function", "size", "scenario", "detail", "comparison", "packing", "care", "selling_point", "white_background", "closing"];
// APIMart 保比例不保精确像素（BE 记 requested vs actual）：mock 忠实映射实返尺寸（[w,h]）。
const ECOM_ACTUAL_DIMS: Record<string, [number, number]> = { "1024x1024": [1254, 1254], "768x1024": [1086, 1448], "1024x1536": [1024, 1536] };
const ECOM_ASPECTS: Record<string, string> = { "1024x1024": "1:1", "768x1024": "3:4", "1024x1536": "2:3" };
// 真 BE EcomReplicatePlanOutput 字段全集（无 preview_url；FIX2 唯一图片 URL 为 download_url）。
interface MockEcomOutput {
  id: string; index: number; theme: string; reference_asset_id: string | null; product_asset_id: string | null;
  requested_size: string; requested_aspect: string; status: string; prompt: string | null;
  asset_id: string | null; download_url: string | null; actual_width: number | null; actual_height: number | null;
}
interface MockEcomJob {
  job_id: string; status: string; output_mode: string; output_count: number; total_credits: number; credit_rate: number;
  requested_size: string; requested_aspect: string; outputs: MockEcomOutput[]; _charged: boolean; _polls: number; _failIndex: number | null;
}
const ecomReplicateJobs = new Map<string, MockEcomJob>();
let ecomReplicateSeq = 0;

function ecomBuildOutputs(jobId: string, mode: string, refs: string[], products: string[], points: string[]): MockEcomOutput[] {
  const count = mode === "main" ? 5 : 12;
  const themes = mode === "main" ? ECOM_MAIN_THEMES : ECOM_DETAIL_THEMES;
  const size = mode === "main" ? "1024x1024" : "768x1024";
  const aspect = ECOM_ASPECTS[size] ?? "1:1";
  const nRefs = Math.max(refs.length, 1);
  const nProd = Math.max(products.length, 1);
  const nPts = Math.max(points.length, 1);
  return Array.from({ length: count }, (_, i) => ({
    id: `${jobId}-o${i}`,
    index: i,
    theme: themes[i] ?? `page_${i}`,
    reference_asset_id: refs[i % nRefs] ?? null,
    product_asset_id: products[i % nProd] ?? null,
    requested_size: size,
    requested_aspect: aspect,
    status: "planned",
    prompt: `复刻参考图构图/摆位/光影，替换为商品；${points[i % nPts] || ""}`.trim(),
    asset_id: null,
    download_url: null,
    actual_width: null,
    actual_height: null
  }));
}
// EcomReplicateAccepted 形状（POST /replicate 与 GET 共用）。
function ecomAcceptedResponse(j: MockEcomJob) {
  return {
    job_id: j.job_id, status: j.status, output_mode: j.output_mode, output_count: j.output_count,
    total_credits: j.total_credits, credit_rate: j.credit_rate, requested_size: j.requested_size, requested_aspect: j.requested_aspect,
    plan: {
      outputs: j.outputs.map((o) => ({
        id: o.id, index: o.index, theme: o.theme, reference_asset_id: o.reference_asset_id, product_asset_id: o.product_asset_id,
        requested_size: o.requested_size, requested_aspect: o.requested_aspect, status: o.status, prompt: o.prompt,
        asset_id: o.asset_id, download_url: o.download_url, actual_width: o.actual_width, actual_height: o.actual_height
      })),
      reference_analysis_json: refs_analysis(j),
      template_mapping_json: { strategy: "cycle_references_and_products" },
      generation_plan_json: { mode: j.output_mode, count: j.output_count }
    }
  };
}
function refs_analysis(j: MockEcomJob): Record<string, unknown>[] {
  return [{ summary: `${j.output_mode} 复刻规划`, outputs: j.output_count }];
}

// ── 图片历史·统一模块 (HISTORY-UI-0001) mock ── 镜像归一契约（需求冻结）：list 分页 + detail 整套，4 category。
// 归一：各类存储 → HistoryItem（卡片）/ HistoryImageSet（整套，每张原图 download_url + 原始尺寸）。海报历史隐藏（不入任何 tab）。
// FIX2 对齐真实 BE：详情单张 download_url/width/height 必填非空（BE 详情只返成功张、失败张已 omit，故无 null）。
interface MockHistItem { index: number; download_url: string; width: number; height: number; theme?: string; label?: string }
interface MockHistRecord {
  id: string; category: string; title: string; cover_url: string; created_at: string; status: string;
  items: MockHistItem[]; meta?: Record<string, unknown>;
}
function histItem(index: number, opts: { dims?: [number, number]; theme?: string; label?: string }): MockHistItem {
  const dims = opts.dims ?? [1254, 1254];
  const url = `https://mock.local/hist/${opts.theme ?? opts.label ?? "img"}-${index}.png`;
  return { index, download_url: `${url}?dl=1`, width: dims[0], height: dims[1], theme: opts.theme, label: opts.label };
}
function histRecord(r: Omit<MockHistRecord, "cover_url"> & { cover_url?: string }): MockHistRecord {
  const cover = r.cover_url ?? r.items.find((it) => it.download_url)?.download_url ?? "https://mock.local/hist/cover.png";
  return { ...r, cover_url: cover };
}
const MAIN_THEMES = ["layout_match", "color_match", "campaign_match", "social_match", "white_background"];
const DETAIL_THEMES = ["hero", "material", "function", "size", "scenario", "detail", "comparison", "packing", "care", "selling_point", "white_background", "closing"];
const histTs = (i: number) => new Date(Date.UTC(2026, 6, 10, 12, 0, 0) - i * 60_000).toISOString(); // 递减 → 倒序稳定
const historyImageRecords: MockHistRecord[] = [
  // 电商详情图（ecom_detail）：主图 5 张(completed) + 详情页 partial_failed（规划 12、成功 11，失败张 BE 已 omit）
  histRecord({
    id: "hd-main-1", category: "ecom_detail", title: "保温杯 · 主图复刻（5 张）", created_at: histTs(0), status: "completed",
    items: MAIN_THEMES.map((t, i) => histItem(i, { dims: [1254, 1254], theme: t })), meta: { output_mode: "main" }
  }),
  histRecord({
    // FIX2 对齐真实 BE：job=partial_failed，但详情**只返成功张**（1 张失败被 BE omit）→ 12 规划、11 成功。
    // 列表 item_count = 成功张数（services/image_history.py:109 `item_count = count(succeeded)`）= 11，非规划 12。
    id: "hd-detail-1", category: "ecom_detail", title: "保温杯 · 详情页（11 张成功）", created_at: histTs(1), status: "partial_failed",
    items: DETAIL_THEMES.slice(0, 11).map((t, i) => histItem(i, { dims: [1086, 1448], theme: t })), meta: { output_mode: "detail" }
  }),
  // 电商模特图（ecom_model）：套图 4 张
  histRecord({
    id: "hm-1", category: "ecom_model", title: "连衣裙 · AI 模特（4 张）", created_at: histTs(2), status: "completed",
    items: Array.from({ length: 4 }, (_, i) => histItem(i, { dims: [1024, 1536], label: "模特图" }))
  }),
  // 电商白底图（ecom_white）：单图恒 ready
  histRecord({ id: "hw-1", category: "ecom_white", title: "陶瓷水杯 · 白底图", created_at: histTs(3), status: "ready", items: [histItem(0, { dims: [1024, 1024], label: "白底图" })] }),
  histRecord({ id: "hw-2", category: "ecom_white", title: "蓝牙耳机 · 白底图", created_at: histTs(4), status: "ready", items: [histItem(0, { dims: [1024, 1024], label: "白底图" })] }),
  // 封面（cover）：HISTORY-IMAGE-TAB-UI-0001 新增第 6 分类（BE 归一 API 加 cover）；单图恒 ready。
  histRecord({ id: "hc-1", category: "cover", title: "咖啡科普 · 封面", created_at: histTs(5), status: "ready", items: [histItem(0, { dims: [1280, 720], label: "封面" })] }),
  histRecord({ id: "hc-2", category: "cover", title: "带货短片 · 封面", created_at: histTs(6), status: "ready", items: [histItem(0, { dims: [1280, 720], label: "封面" })] }),
  // 图片生成/修改（image_gen）：生成 23 条单图 → 触发分页「加载更多」（page_size 20）
  ...Array.from({ length: 23 }, (_, i) =>
    histRecord({ id: `hg-${i + 1}`, category: "image_gen", title: `创意图 #${i + 1}`, created_at: histTs(10 + i), status: "ready", items: [histItem(0, { dims: [1024, 1024], label: "图片生成" })] })
  )
];
const historyToItem = (r: MockHistRecord) => ({
  id: r.id, category: r.category, title: r.title, cover_url: r.cover_url, created_at: r.created_at, status: r.status, item_count: r.items.length
});
// FIX2 对齐真实 BE：ImageHistoryCategory 5 枚举（schemas/history.py:6-12）。非法 category → 422（BE FastAPI 校验 Literal）。
const VALID_HISTORY_CATEGORIES = new Set(["image_gen", "ecom_white", "ecom_model", "ecom_detail", "cover"]);

export const handlers = [
  // ── 图片历史·统一模块 (HISTORY-UI-0001)：list 分页 + detail 整套（list 先注册，避免被 /:category/:id 影子覆盖）──
  http.get(`${BASE}/api/v1/history/images`, ({ request }) => {
    const sp = new URL(request.url).searchParams;
    const category = sp.get("category"); // 省略（HISTORY-IMAGE-TAB-UI-0001）= 全部图片
    // FIX2：非法 category → 422（红线：mock 不能比 BE 宽松；BE FastAPI 对 Literal query 参数返 422，不是 200 空列表）。
    if (category !== null && !VALID_HISTORY_CATEGORIES.has(category)) return err(422, "VALIDATION_ERROR", "无效的图片分类");
    const page = Math.max(1, Number(sp.get("page") ?? 1));
    const pageSize = Math.max(1, Math.min(100, Number(sp.get("page_size") ?? 20)));
    // 租户作用域 + 按 created_at 倒序（seed 已按倒序时间戳）。无 category → 全部分类混合。
    const all = historyImageRecords
      .filter((r) => !category || r.category === category)
      .slice()
      .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
    const start = (page - 1) * pageSize;
    return ok({ items: all.slice(start, start + pageSize).map(historyToItem), total: all.length, page, page_size: pageSize });
  }),
  http.get(`${BASE}/api/v1/history/images/:category/:id`, ({ params }) => {
    // FIX2：非法 category → 422（BE 对 Literal path 参数先校验，先于 404）。
    if (!VALID_HISTORY_CATEGORIES.has(String(params.category))) return err(422, "VALIDATION_ERROR", "无效的图片分类");
    const rec = historyImageRecords.find((r) => r.category === String(params.category) && r.id === String(params.id));
    if (!rec) return err(404, "HISTORY_NOT_FOUND", "记录不存在或无权访问");
    return ok({ id: rec.id, category: rec.category, created_at: rec.created_at, status: rec.status, items: rec.items, meta: rec.meta ?? {} });
  }),
  // FIX1（HISTORY-IMAGE-TAB-UI-0001）：图片删除端点被摘（用户「三拆」改 GC 方案）→ 此处不再 mock 图片删除端点（不留死代码）。
  // Auth = M2 shapes (unchanged). Mocked so the (app) client auth-gate can be
  // passed during the MSW parallel period without a real backend.
  http.post(`${BASE}/api/v1/auth/login`, () =>
    ok({ access_token: "mock-token", token_type: "bearer", tenant_id: "ten-mock", user_id: "u-mock", role: resolveMockState().role })
  ),
  // 注册（AUTH-UI-0001 · FIX1 硬化）：镜像 BE POST /auth/register-tenant → {tenant,user,token}（201）。
  // 校验必填 + extra="forbid" + full_name≤200（防非法请求在 mock 假绿，P2）；slug="taken" → 409。
  http.post(`${BASE}/api/v1/auth/register-tenant`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    // 镜像 BE extra="forbid"：只允许这 5 键，多余即 422。
    const ALLOWED = ["tenant_slug", "tenant_name", "email", "password", "full_name"];
    const extra = Object.keys(body).filter((k) => !ALLOWED.includes(k));
    if (extra.length) return err(422, "VALIDATION_ERROR", `Extra inputs are not permitted: ${extra.join(",")}`);
    // 必填 + 基本长度（对齐 BE TenantRegisterRequest；防漏字段/超长假绿）。
    const slug = body.tenant_slug;
    const name = body.tenant_name;
    const email = body.email;
    const password = body.password;
    const fullName = body.full_name;
    if (typeof slug !== "string" || !/^[a-z0-9][a-z0-9-]*$/.test(slug) || slug.length < 2 || slug.length > 80)
      return err(422, "VALIDATION_ERROR", "invalid tenant_slug");
    if (typeof name !== "string" || name.length < 1 || name.length > 200)
      return err(422, "VALIDATION_ERROR", "invalid tenant_name");
    if (typeof email !== "string" || !email.includes("@") || email.length < 3 || email.length > 320)
      return err(422, "VALIDATION_ERROR", "invalid email");
    if (typeof password !== "string" || password.length < 8 || password.length > 128)
      return err(422, "VALIDATION_ERROR", "invalid password");
    if (fullName !== undefined && (typeof fullName !== "string" || fullName.length > 200))
      return err(422, "VALIDATION_ERROR", "invalid full_name");
    if (slug === "taken") return err(409, "tenant_slug_taken", "Tenant slug is already taken.");
    const registered = {
      tenant: { id: "ten-new", slug, name },
      user: { id: "u-new", tenant_id: "ten-new", email, full_name: (fullName as string) ?? null, role: "admin" as const }
    };
    // 🔴 PROD-P0-...-FIX2：真实注册链**只写 hd_mock_registered 这一个 key**——resolveMockState() 见它即权威推导
    // 「非平台 + free + role admin」（owner 在真实 BE 就是 ADMIN，保留此事实，正是线上那个坑）。register() 成功后必调
    // /auth/me；单一状态源保证 /me 身份(ten-new) + 权限(三 entitlement 全无) + analytics 门禁(403) **全部一致**，
    // 不再需要另写 platform/plan/role 旋钮（它们由注册身份直接推导）。
    try {
      if (typeof localStorage !== "undefined") {
        localStorage.setItem("hd_mock_registered", JSON.stringify(registered));
      }
    } catch {
      /* localStorage 不可用 → 无注册身份，resolveMockState 回落旋钮（身份为 ten-mock；仅极端环境） */
    }
    return HttpResponse.json(
      {
        data: {
          tenant: registered.tenant,
          user: registered.user,
          token: { access_token: "mock-token", token_type: "bearer", tenant_id: "ten-new", user_id: "u-new", role: "admin" }
        },
        error: null,
        request_id: "mock-req"
      },
      { status: 201 } // 对齐 BE：注册成功 201 CREATED
    );
  }),
  http.get(`${BASE}/api/v1/auth/me`, () => {
    // 身份 + 权限**同源** resolveMockState()：走过注册链 → 刚注册的 ten-new + 非平台 free（三 entitlement 全无）；
    // 否则默认 ten-mock + 旋钮派生。与 analyticsHandlers 读同一状态源，杜绝「/me 说没权限、analytics 却返全站」的错配。
    const state = resolveMockState();
    return ok({
      tenant: state.identity?.tenant ?? { id: "ten-mock", slug: "huading", name: "华鼎（mock）" },
      user: state.identity?.user ?? { id: "u-mock", tenant_id: "ten-mock", email: "qa@huading.test", full_name: "QA 测试", role: state.role },
      permissions: mockPermissions(state)
    });
  }),
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
    const body = (await request.json()) as { topic?: string; length_tier?: string; duration_sec?: number };
    // topic 必填（BE ScriptGenerateRequest.topic 非可选）——mock 不比 BE 宽松：缺/空即 422，守住前端两处调用方
    // （电商 onGenerateScript、口播 onGenerateScript）都在 topic 非空时才发。
    if (!body.topic || !body.topic.trim()) {
      return err(422, "VALIDATION_ERROR", "topic is required");
    }
    // FIX2（CB P1 · 机制）：BE ScriptGenerateRequest.duration_sec 是 int——小数 → 422。此前 scripts mock 没读该字段 =
    // 「AI生成文案」发小数悄悄假绿的根因。任何接受 duration_sec 的 mock 都拒绝非整数，未加门的路径即在测试里响亮失败。
    if (badDuration(body.duration_sec)) return err(422, "VALIDATION_ERROR", "duration_sec 必须为整数");
    // 字数档位（ECOM-VIDEO-OPTIMIZE-UI-0001 契约 §4.5）：可选，present 时须 short/medium/long（镜像 BE Literal，
    // mock 不比 BE 宽松——非法枚举即 422，守住前端只发合法档位）。反映到 mock 文案长度供承重区分档位真接线。
    if (body.length_tier !== undefined && !["short", "medium", "long"].includes(body.length_tier)) {
      return err(422, "VALIDATION_ERROR", "length_tier 非法");
    }
    const tierWords: Record<string, string> = { short: "（短）", medium: "（中）", long: "（长，更详尽的卖点展开……）" };
    const suffix = body.length_tier ? tierWords[body.length_tier] : "";
    return ok({ script: `【${body.topic}】大家好，今天用一分钟带你了解……（mock 文案，可编辑）${suffix}` });
  }),
  // 「AI 生成画面」scene-prompt（ECOM-VIDEO-OPTIMIZE-UI-0001 契约 §4.2）：从只收 topic → 收产品图 keys + 文案 + topic。
  // 🔴 严格纪律「必须带产品图」——mock 不比 BE 宽松：product_image_keys 缺失/空 → 422（luna 多模态强制读图）。
  // 返回 {scene_prompt, negative_prompt}（新增 negative_prompt，前端自动填入负面框）。
  http.post(`${BASE}/api/v1/videos/scene-prompt`, async ({ request }) => {
    const body = (await request.json()) as { topic?: string; script?: string; product_image_keys?: string[]; duration_sec?: number };
    if (!Array.isArray(body.product_image_keys) || body.product_image_keys.length < 1) {
      return err(422, "VALIDATION_ERROR", "product_image_keys 至少 1 张");
    }
    // FIX1（CB P1）：duration_sec 是 int——小数/字符串 → 422（镜像 BE，不四舍五入放行；此前 round 5.5→6 是假绿，线上真 422）。
    if (badDuration(body.duration_sec)) return err(422, "VALIDATION_ERROR", "duration_sec 必须为整数");
    // SCENE-DURATION-FIX：duration_sec 可选整数，镜像 BE ScenePromptRequest._clamp_duration 夹取 [5,120]（不 reject 越界，仅夹取）。
    // 把生效时长回写进 scene_prompt 秒数——真 BE 由 luna 据时长写节奏，mock 以此如实反映「秒数随所选时长变化」（此前恒「约15秒」即原 bug）。
    const seconds =
      typeof body.duration_sec === "number"
        ? Math.max(5, Math.min(120, body.duration_sec))
        : 15; // 未传时回退 15（正是漏传 duration 的旧表现，便于承重/变异对照）
    return ok({
      scene_prompt: `白色大理石台面暖光特写，产品缓慢环绕运镜，浅景深突出材质，蒸汽轻升，节奏舒缓，约 ${seconds} 秒（mock 专业画面提示词，可编辑）`,
      negative_prompt: "低分辨率, 变形, 多余文字, 水印, 杂乱背景, 手部畸变"
    });
  }),
  http.post(`${BASE}/api/v1/uploads/images`, () => {
    const n = ++imageUploadSeq;
    const asset_id = `upload-${n}`;
    // AIBRAIN-UI-0001 · FIX4：登记到唯一资产注册表 → 智脑发消息时才查得到（不再凭空伪造，见 asset-registry.ts）。
    registerMockAsset({ asset_id, asset_type: "avatar_image", mime_type: "image/png", status: "ready", download_url: `https://mock.local/u${n}.jpg` });
    return HttpResponse.json(
      { data: { asset_id, type: "avatar_image", status: "ready", thumbnail_url: `https://mock.local/u${n}.jpg` }, error: null, request_id: "mock-req" },
      { status: 201 }
    );
  }),
  // 数字人出镜视频源上传（AVATAR-VIDEO-SOURCE-UI-0001，镜像 /uploads/images·/uploads/audio）→ {asset_id}。
  // 端点/形状以 BE 包(AVATAR-VIDEO-SOURCE-BE-0001)为准，mock 先行。
  http.post(`${BASE}/api/v1/uploads/videos`, () => {
    const n = ++imageUploadSeq;
    return HttpResponse.json(
      { data: { asset_id: `video-asset-${n}`, type: "avatar_video", status: "ready" }, error: null, request_id: "mock-req" },
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
  // 提示词反推（REVERSE-PROMPT-UI · FIX1 对齐 BE）：请求体仅 source_asset_id（多发 forbid→422）→ 同步返回
  // ReversePromptJobRead(status="succeeded" + result)。读 .id（非 jobId）。
  http.post(`${BASE}/api/v1/reverse-prompt`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const sourceAssetId = body.source_asset_id;
    if (!sourceAssetId) return err(422, "VALIDATION_ERROR", "source_asset_id is required");
    // 镜像 BE extra="forbid"：多余键即 422（守住「请求体只发 source_asset_id」）。
    const extra = Object.keys(body).filter((k) => k !== "source_asset_id" && k !== "target_format");
    if (extra.length) return err(422, "VALIDATION_ERROR", `Extra inputs are not permitted: ${extra.join(",")}`);
    // 后端据资产推 source_kind：视频源（video-asset-*）→ 异步 202 queued（无 result），前端轮询 GET 到终态。
    // 🔴 FIX3：两个分支都**写进同一个 store** —— 原先图片分支只是现编一份 read 就返回、job 根本没落地，
    // 这才是「rp-* 只能放行」的根因。
    if (typeof sourceAssetId === "string" && sourceAssetId.startsWith("video-")) {
      const id = `rpv-${++reverseSeq}`;
      const job = mkReverseJob({ id, source_kind: "video", status: "queued" }); // BE 202 queued（非 running）
      reverseJobs.set(id, job);
      return HttpResponse.json({ data: reverseJobRead(job), error: null, request_id: "mock-req" }, { status: 202 });
    }
    // 图片源：BE 是**同步**的（services:148 置 running → 立刻调 provider → mark_..._succeeded）→ succeeded/200。
    const id = `rp-${++reverseSeq}`;
    const job = mkReverseJob({
      id,
      source_kind: "image",
      status: "succeeded",
      source_thumbnail_url: `https://mock.local/reverse/${id}.png`,
      summary: REVERSE_RESULT.prompt_zh.slice(0, 160) // BE summary = prompt_zh 优先、截断 160（services:332-339）
    });
    reverseJobs.set(id, job);
    return ok(reverseJobRead(job));
  }),
  // 反推历史列表（HISTORY-VIDEO-REVERSE-UI-0001）—— **先于 /jobs/:id 注册**，避免被影子路由覆盖。
  // 🔴 校验镜像 BE、不得更宽松：source_kind 非枚举 → 422（BE 是 Literal 校验，不是 200 空列表）；
  //    page ge=1 / page_size ge=1 le=100（routes:74-75）；省略 source_kind = 全部。
  http.get(`${BASE}/api/v1/reverse-prompt/jobs`, ({ request }) => {
    const sp = new URL(request.url).searchParams;
    const kind = sp.get("source_kind");
    if (kind !== null && kind !== "image" && kind !== "video") return err(422, "VALIDATION_ERROR", "无效的反推来源");
    // 🔴 FIX2 · P1-2：BE 是 `page: Query(ge=1)` / `page_size: Query(ge=1, le=100)`（routes:74-75）→ 越界即 **422**。
    // 旧 mock 把越界值 clamp 成合法值并返 200 = **比 BE 宽松**（本项目第三次犯：#159 / #166 都栽过）——
    // 生产必失败的请求在本地全绿。
    const rawPage = sp.get("page");
    const rawPageSize = sp.get("page_size");
    const page = rawPage === null ? 1 : Number(rawPage);
    const pageSize = rawPageSize === null ? 20 : Number(rawPageSize);
    if (!Number.isInteger(page) || page < 1) return err(422, "VALIDATION_ERROR", "page 必须 ≥ 1");
    if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100)
      return err(422, "VALIDATION_ERROR", "page_size 必须在 1–100 之间");
    // 🔴 FIX3：从**唯一 store** 取；软删过滤只认 deleted_at（BE live_reverse_prompt_job_condition，services:276）。
    const all = [...reverseJobs.values()]
      .filter((j) => j.deleted_at === null)
      .filter((j) => !kind || j.source_kind === kind)
      .sort((a, b) => (a.created_at < b.created_at ? 1 : -1)); // created_at 倒序
    const start = (page - 1) * pageSize;
    return ok({
      items: all.slice(start, start + pageSize).map(reverseHistItem), // 6 字段、无 result
      total: all.length,
      page,
      page_size: pageSize
    });
  }),
  // 软删一条（BE 只置 deleted_at、不碰媒体/Asset）。不存在 / 已删 → 404。
  // 🔴 FIX3：走统一守卫 liveJob；错误码统一为 REVERSE_PROMPT_JOB_NOT_FOUND —— 此处原先自己编了个
  // `REVERSE_JOB_NOT_FOUND`，与 GET 详情的码不一致（同一个 BE 错误、两个 handler 两个码）。
  http.delete(`${BASE}/api/v1/reverse-prompt/jobs/:id`, ({ params }) => {
    const job = liveJob(String(params.id));
    if (!job) return jobNotFound();
    job.deleted_at = new Date(0).toISOString();
    return ok({ id: job.id, deleted_at: job.deleted_at });
  }),
  // 详情：走统一守卫 → 不存在 / 已删一律 404（BE reverse_prompt_job_or_404 + live selector，services:365-368）。
  // 🔴 FIX3：原先这里有个 `if (id.startsWith("rp-")) return ok(reverseJobRead(id))` 放行分支 ——
  // 它不是疏忽，是**没得选**：POST 图片反推产生的 rp-* job 当时根本没落地，除了现编无路可走。
  // job 落进 store 之后，这个分支自然消失。
  http.get(`${BASE}/api/v1/reverse-prompt/jobs/:id`, ({ params }) => {
    const job = liveJob(String(params.id));
    if (!job) return jobNotFound();
    // 视频异步：queued/running 每被轮询一次 +1，第 2 次起转 succeeded（含 result.video_analysis）。
    if (job.source_kind === "video" && (job.status === "queued" || job.status === "running")) {
      job.polls += 1;
      if (job.polls >= 2) job.status = "succeeded";
    }
    return ok(reverseJobRead(job));
  }),
  /**
   * regenerate —— 🔴 FIX3 逐条对齐 BE（读的是源码，不是转述）：
   *  · 不存在 / 已删 → 404（reverse_prompt_job_or_404）
   *  · **video**：`prepare_reverse_prompt_video_retry`（services:178-230）
   *      - status ∈ {queued, running} → **409 REVERSE_PROMPT_ALREADY_RUNNING**
   *        ← 这条**任务包的表里没有**，是我读 BE 时发现的第 5 处宽松
   *      - 否则重置为 **queued**、清空 result → 路由置 **202**（routes:183-185）
   *  · **image**：BE 是同步的（services:148 置 running → 立刻调 provider → mark_..._succeeded）→ succeeded/200
   * 上一版对**任意** id 恒返一份「image succeeded」——视频 job 被伪造成图片、异步被伪造成同步。
   */
  http.post(`${BASE}/api/v1/reverse-prompt/jobs/:id/regenerate`, ({ params }) => {
    const job = liveJob(String(params.id));
    if (!job) return jobNotFound();
    if (job.source_kind === "video") {
      if (job.status === "queued" || job.status === "running")
        return err(409, "REVERSE_PROMPT_ALREADY_RUNNING", "该反推任务正在进行中");
      job.status = "queued";
      job.polls = 0;
      job.saved_at = null;
      job.error_code = null;
      job.error_message = null;
      job.summary = null; // BE 清 result_json → summary 随之为空
      return HttpResponse.json({ data: reverseJobRead(job), error: null, request_id: "mock-req" }, { status: 202 });
    }
    job.status = "succeeded";
    job.saved_at = null;
    job.error_code = null;
    job.error_message = null;
    job.summary = REVERSE_RESULT.prompt_zh.slice(0, 160);
    return ok(reverseJobRead(job));
  }),
  /**
   * save —— 🔴 FIX3 逐条对齐 BE（services:245-263）：
   *  · 不存在 / 已删 → 404
   *  · `status not in {succeeded, saved}` → **409 REVERSE_PROMPT_NOT_READY**
   *    注意是**两个**终态放行：`saved` 也放行 → **重复保存幂等**，不是只有 succeeded（任务包只提了 succeeded）
   *  · 否则 status="saved" + saved_at=now
   * 上一版对**任意** id、**任意**状态恒成功 —— queued/running/failed 全放行，与 BE 正相反。
   */
  http.post(`${BASE}/api/v1/reverse-prompt/jobs/:id/save`, ({ params }) => {
    const job = liveJob(String(params.id));
    if (!job) return jobNotFound();
    if (job.status !== "succeeded" && job.status !== "saved")
      return err(409, "REVERSE_PROMPT_NOT_READY", "只有已完成的反推结果才能保存");
    job.status = "saved";
    job.saved_at = new Date(0).toISOString();
    // BE ReversePromptSavedResponse = { id, status: Literal["saved"]="saved", saved_at }（schemas:86-89）——
    // status 有默认值但**照样进响应**，不是可省字段。（我重构时漏了它，是既有 adapter 测试把我抓回来的：
    // 那条测试是对的，「让既有测试跟着收敛」在这一项上会是**改测试来掩盖 mock 变得更不忠实**。）
    return ok({ id: job.id, status: "saved", saved_at: job.saved_at });
  }),
  // 预计积分（ECOM-VIDEO-OPTIMIZE-UI-0001 · FIX2 · P1）：确认窗打开必调 POST /videos/estimate。此前缺该 handler
  // → MSW 放行到真后端 → CI net::ERR_FAILED。响应契约逐字对齐 #202（backend/app/api/v1/routes/videos.py:1149-1166
  //  返 ApiResponse[VideoEstimateResponse]{estimated_credits:int, unit:"credits", note:str|None}；schemas/videos.py:297-300）。
  // 校验镜像 videos POST 同源（resolution 全模式 + seedance_i2v 产品图≥1/voice_id）——estimate 用同一 VideoGenerateRequest
  // (extra=forbid)，mock 不比 BE 宽松（防假绿：接线断/缺参在 estimate 阶段即暴露，不放到提交才红）。
  http.post(`${BASE}/api/v1/videos/estimate`, async ({ request }) => {
    const body = (await request.json()) as {
      video_mode?: string;
      product_image_keys?: string[];
      voice_id?: string;
      duration_sec?: number;
      resolution?: string;
      aspect_ratio?: string; // VIDEO-GEN-PARAMS-UI-0001：estimate 与提交同校验（视频生成 7 值）
      reference_image_asset_ids?: string[]; // V2V：estimate 与提交同门（互斥/条数），别重演「estimate 比提交宽松」
      reference_video_asset_ids?: string[];
    };
    if (body.resolution !== undefined && !VIDEO_GEN_RESOLUTIONS.includes(body.resolution)) {
      return err(422, "VALIDATION_ERROR", "resolution 非法");
    }
    if (badDuration(body.duration_sec)) return err(422, "VALIDATION_ERROR", "duration_sec 必须为整数"); // FIX1：estimate 用同一 VideoGenerateRequest(int)
    if (body.video_mode === "seedance_i2v") {
      const keys = body.product_image_keys ?? [];
      if (!Array.isArray(keys) || keys.length < 1 || keys.length > 9) {
        return err(422, "ECOM_I2V_INVALID", "电商带货产品图 1–9 张");
      }
      if (!body.voice_id) return err(422, "ECOM_I2V_INVALID", "电商带货需选择音色");
    }
    // VIDEO-GEN-PARAMS-UI-0001（Code Review）：estimate 与提交同一 VideoGenerateRequest 校验——video_gen 的
    // 时长/比例门也要在 estimate 阶段拦（此前缺失 → estimate 200 而提交 422，estimate 比提交宽松=假绿口）。
    if (body.video_mode === "video_gen") {
      if (!Number.isInteger(body.duration_sec) || (body.duration_sec as number) < 4 || (body.duration_sec as number) > 15) {
        return err(422, "VIDEO_GEN_INVALID", "视频生成时长须为 4–15 的整数秒");
      }
      if (
        body.aspect_ratio !== undefined &&
        !["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "auto"].includes(body.aspect_ratio)
      ) {
        return err(422, "VIDEO_GEN_INVALID", "画面比例非法");
      }
      // V2V（Code Review 自查·同上轮教训）：estimate 与提交同门——图+视频互斥、视频 ≤3 条也在 estimate 阶段拦。
      const estRefs = body.reference_image_asset_ids ?? [];
      const estVideos = body.reference_video_asset_ids ?? [];
      if (estRefs.length > 0 && estVideos.length > 0) {
        return err(422, "VIDEO_GEN_MEDIA_CONFLICT", "参考图与参考视频只能二选一");
      }
      if (estVideos.length > 3) return err(422, "VIDEO_GEN_INVALID", "参考视频最多 3 条");
    }
    // estimated_credits 是后端按配额/时长算出的整数；mock 取时长派生一个正整数（默认 30s→12），仅需形状忠实（值非契约）。
    const estimatedCredits = typeof body.duration_sec === "number" && body.duration_sec > 0
      ? Math.max(1, Math.round(body.duration_sec / 2.5))
      : 12;
    return ok({
      estimated_credits: estimatedCredits,
      unit: "credits",
      note: "Estimated reservation; final settlement uses actual generated duration."
    });
  }),
  http.post(`${BASE}/api/v1/videos`, async ({ request }) => {
    const body = (await request.json()) as {
      topic?: string;
      video_mode?: string;
      purpose?: string;
      prompt?: string;
      reference_image_asset_ids?: string[];
      reference_video_asset_ids?: string[]; // 视频生视频（V2V-UI-0001，≤3、与参考图互斥 D8；mock 先行）
      product_image_keys?: string[]; // 电商带货 i2v 产品图（ECOM-VIDEO-OPTIMIZE-UI-0001 §4.3）
      negative_prompt?: string; // 电商带货 i2v 负面 / 图片负面（photo 复用，IMAGE-GEN-OPTIMIZE-UI-0001 §四）
      voice_id?: string; // 电商带货/数字人口播必填
      duration_sec?: number;
      resolution?: string;
      bgm?: { source?: string; asset_id?: string; track_id?: string };
      apply_visible_label?: boolean;
      avatar_asset_id?: string;
      avatar_video_asset_id?: string;
      aspect_ratio?: string; // 画面比例（视频生成 7 值 / 图片 8+auto，VIDEO-GEN-PARAMS-UI-0001 需求3）
      generate_audio?: boolean; // 视频生成音频开关（VIDEO-GEN-PARAMS-UI-0001 需求4，随请求传）
      // 图片生成/修改 photo 优化（IMAGE-GEN-OPTIMIZE-UI-0001 §四）
      image_keys?: string[]; // 参考图 1–6（可选）
      master_prompt?: string; // 任务总控（可选）
      master_negative_prompt?: string; // 任务统一负面（可选）
      similarity_strength?: number; // 三个强度：10..100 步长 10，未开启不出现（背景参考强度已砍除·从未上线）
      creativity_strength?: number;
      subject_strength?: number;
      image_resolution?: string; // §3之二：清晰度档位 1k/2k/4k
    };
    // resolution 是后端全模式 Literal["480p","720p","1080p"]（含 seedance_i2v，见 schemas/videos.py:154）：
    // 非法即 422，不按模式放宽（ECOM-RESOLUTION-UI-0001：电商也带 resolution，需与 video_gen 同等把关，不伪造放行）。
    if (body.resolution !== undefined && !VIDEO_GEN_RESOLUTIONS.includes(body.resolution)) {
      return err(422, "VALIDATION_ERROR", "resolution 非法");
    }
    // FIX1（CB P1 · 提交路径核查）：VideoGenerateRequest.duration_sec 也是 int——小数 → 422（前端 isValidDuration 已从源头拦，
    // 此为 mock 防漂移把关，不比 BE 宽松；提交路径此前也能漏小数，属既有 bug，一并堵住）。
    if (badDuration(body.duration_sec)) return err(422, "VALIDATION_ERROR", "duration_sec 必须为整数");
    // 提示词 2000 字墙（VIDEO-GEN-PARAMS-UI-0001 需求2/D4）：逐字镜像 BE schemas/videos.py:307-312——**所有非 photo 模式**
    // 的 topic 超 2000 → 422（video_gen 把 prompt 同时发作 topic，故超 2000 在此拦；photo 自有 20000 上界，走下方分支）。
    // Code Review：按**码点**计数（Array.from）对齐 Python len()——JS .length 是 UTF-16 码元，增补面 emoji 记 2 会比 BE 严（误杀）。
    if (body.video_mode !== "photo" && typeof body.topic === "string" && Array.from(body.topic.trim()).length > 2000) {
      return err(422, "VALIDATION_ERROR", "提示词输入最大上限为 2000 字");
    }
    // 数字人形象源二选一互斥（AVATAR-VIDEO-SOURCE-UI-0001）：照片 avatar_asset_id 与视频 avatar_video_asset_id
    // 不可同发（BE 权威，mock 先行守住互斥）。前端只发其一，此为防漂移。
    if (body.avatar_asset_id && body.avatar_video_asset_id) {
      return err(422, "AVATAR_SOURCE_CONFLICT", "照片与视频形象只能二选一");
    }
    // 视频生成 video_gen 校验（FIX1 真联调对齐 #213 合并源 schemas/videos.py:349-366：参考图 **0–9** 且**唯一**
    // （0 张合法=纯文生视频；UI 的 ≥1 门属前端保守、待需求6 拆批放开）、prompt 非空、duration 整数 4–15、画面比例
    // 7 值、bgm 二选一可选）→ 非法 422，不伪造放行/不放宽（resolution 已在上方全模式把关；2000 墙在上方非-photo 段拦）。
    if (body.video_mode === "video_gen") {
      const refs = body.reference_image_asset_ids ?? [];
      const refsUnique = new Set(refs).size === refs.length; // 对齐后端唯一性校验（重复→422）
      // 视频生视频（V2V-UI-0001 D8/D10，mock 先行·不比 BE 宽松也不比 BE 严）：
      //  · 图与视频**严格二选一**（provider image_with_roles 与 video_urls 不能同用）→ 同传 422；
      //  · 参考视频 ≤3 条（provider 上限）、唯一 → 4 条/重复 422。合计时长 1.8–15.2s 由前端预检把关
      //    （mock 收 asset_id 无时长可验；BE 权威校验在上传/提交侧，真联调对齐）。
      const refVideos = body.reference_video_asset_ids ?? [];
      const refVideosUnique = new Set(refVideos).size === refVideos.length;
      if (refs.length > 0 && refVideos.length > 0) {
        return err(422, "VIDEO_GEN_MEDIA_CONFLICT", "参考图与参考视频只能二选一");
      }
      if (!Array.isArray(refVideos) || refVideos.length > 3 || !refVideosUnique) {
        return err(422, "VIDEO_GEN_INVALID", "参考视频最多 3 条且不可重复");
      }
      const bgmOk =
        body.bgm === undefined ||
        (body.bgm.source === "upload" && !!body.bgm.asset_id) ||
        (body.bgm.source === "library" && !!body.bgm.track_id && BGM_TRACK_IDS.includes(body.bgm.track_id));
      // 时长（VIDEO-GEN-PARAMS-UI-0001 需求5）：预设 5/10/15 + 自定义**整数 4–15**（BE _VIDEO_GEN_MIN/MAX_DURATION_SEC，
      // None 也拒）。3/16/20→422（越界），5.5 已由上方 badDuration 拦。mock 不比 BE 宽松、不放宽到旧的仅 5/10/15。
      const durationOk =
        Number.isInteger(body.duration_sec) && (body.duration_sec as number) >= 4 && (body.duration_sec as number) <= 15;
      if (
        !Array.isArray(refs) ||
        refs.length > 9 ||
        !refsUnique ||
        !body.prompt ||
        !body.prompt.trim() ||
        !durationOk ||
        !bgmOk
      ) {
        return err(422, "VIDEO_GEN_INVALID", "视频生成参数非法");
      }
      // 画面比例（需求3）：present 时须 7 值之一，非法→422（mock 不比 BE 宽松；provider _VALID_SIZES 正好这 7 个）。
      // ⚠️ 有意不 import 前端 VIDEO_ASPECT_RATIOS：mock 独立镜像 **BE** 值域（防漂移设计，同 VIDEO_GEN_RESOLUTIONS 模式）——
      // 前端若单方面加值，此处 422 恰是护栏；仅当 BE 同步支持时才在此同步（4–15 时长同理，权威=apimart.py:209）。
      if (
        body.aspect_ratio !== undefined &&
        !["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "auto"].includes(body.aspect_ratio)
      ) {
        return err(422, "VIDEO_GEN_INVALID", "画面比例非法（须为 16:9/9:16/1:1/4:3/3:4/21:9/auto）");
      }
    }
    // 电商带货 i2v 校验（ECOM-VIDEO-OPTIMIZE-UI-0001 契约 §4.3）：产品图下限=至少 1 张（决策2 保底；topic/script/
    // scene 可全空但产品图必带）。mock 不比 BE 宽松——缺 product_image_keys 即 422，守住前端「产品图 ≥1」门。
    if (body.video_mode === "seedance_i2v") {
      const keys = body.product_image_keys ?? [];
      if (!Array.isArray(keys) || keys.length < 1 || keys.length > 9) {
        return err(422, "ECOM_I2V_INVALID", "电商带货产品图 1–9 张");
      }
      // 电商带货音色必填（BE：数字人口播/电商带货 voice_id 必填）——mock 不比 BE 宽松，守住前端 generateDisabled 的音色门。
      if (!body.voice_id) {
        return err(422, "ECOM_I2V_INVALID", "电商带货需选择音色");
      }
    }
    // 图片生成/修改 photo 校验（IMAGE-GEN-OPTIMIZE-UI-0001 契约 §四）。mock 不比 BE 宽松：
    //  · image_keys 若present 须 1–6（7 张 → 422）——可选（纯文生图 / AI 封面均不带，不误杀）；
    //  · 三个强度若present 须 10..100 步长 10（非法 → 422）；未开启的强度**不应出现**（前端已保证，此为防漂移）。
    // ⚠️ 零回归：AI 封面（purpose:cover，只带 image_size/image_quality，无 image_keys/强度）在此天然全过。
    if (body.video_mode === "photo") {
      if (body.image_keys !== undefined) {
        const keys = body.image_keys;
        // FIX1 真联调：数量 1–6 且每个 key 须匹配 BE 格式（uploads/<name>.{jpg,jpeg,png,webp}）——mock 此前只卡数量、放行「a」等假 key（比 BE 宽松）。
        if (
          !Array.isArray(keys) ||
          keys.length < 1 ||
          keys.length > 6 ||
          keys.some((k) => typeof k !== "string" || !PHOTO_IMAGE_KEY_RE.test(k))
        ) {
          return err(422, "PHOTO_INVALID", "参考图 1–6 张，且 key 须为 uploads/<name>.{jpg,jpeg,png,webp}");
        }
      }
      if (
        badStrength(body.similarity_strength) ||
        badStrength(body.creativity_strength) ||
        badStrength(body.subject_strength)
      ) {
        return err(422, "PHOTO_INVALID", "强度取值须为 10..100 步长 10");
      }
      // FIX1 真联调：四层提示词各 ≤20000（BE schema 校验，超限 422，非静默截断）。
      if (overLen(body.topic) || overLen(body.master_prompt) || overLen(body.master_negative_prompt) || overLen(body.negative_prompt)) {
        return err(422, "PHOTO_INVALID", "提示词最多 20000 字符");
      }
      // §3之二 清晰度档位：present 时须 1k/2k/4k（mock 不比 BE 宽松）。AI 封面不带 → 天然放过。
      if (body.image_resolution !== undefined && !["1k", "2k", "4k"].includes(body.image_resolution)) {
        return err(422, "PHOTO_INVALID", "image_resolution 须为 1k/2k/4k");
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
    const body = (await request.json()) as { source_text: string; mode: string; n?: number; duration_sec?: number };
    // FIX3（CB P2-1 · 机制按"接受方"算）：CopyRewriteRequest.duration_sec 是 int（copy.py，extra=forbid）——小数 → 422。
    // 该端点当前无发送方，但类型支持该字段；凡**接受** duration_sec 的 mock 一律拒非整数，堵住"将来有人发就假绿"。
    if (badDuration(body.duration_sec)) return err(422, "VALIDATION_ERROR", "duration_sec 必须为整数");
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
  // 忠实后端 EcomModelStyle(仅 id+name)。ECOM-MODEL-OPTIMIZE-UI-0001 · D3：6 档风格。
  // 🔴 FIX1 真联调：id + name **逐字对齐 #210 合并源** routes/ecom_images.py:70-95 `_MODEL_STYLE_DEFINITIONS`
  //   （新增 3 档 id 修正为 office_commute / resort_travel / high_fashion——此前 mock 的 commute/vacation/editorial
  //   与 BE 对不上，真接口会 ECOM_MODEL_STYLE_INVALID）。名称随后端返回，前端拉取渲染不硬编码；风格可选。
  http.get(`${BASE}/api/v1/ecom-images/model-styles`, () =>
    ok({
      styles: [
        { id: "studio_white", name: "棚拍白底" },
        { id: "lifestyle", name: "生活场景" },
        { id: "street", name: "街拍" },
        { id: "office_commute", name: "通勤职场" },
        { id: "resort_travel", name: "度假旅拍" },
        { id: "high_fashion", name: "高级时尚大片" }
      ]
    })
  ),
  // ECOM-MODEL-OPTIMIZE-UI-0001 · 单张 AI 模特 mock —— 🔴 FIX1 真联调：逐字段/逐错误码对齐 **#210 合并源**
  //  （schemas/ecom_images.py:55-98 + routes/ecom_images.py:174-182）：extra=forbid → VALIDATION_ERROR；
  //  商品图 1–N(0→422) + 模特图 0–N + 合计 ≤6(超→422) + product_images_mode 合法值 + style_id 与 custom_style 互斥
  //  (**BE 是拒绝而非取其一**：validator raise) + extra_prompt/custom_style ≤20000(超→422) + 未知 style_id →
  //  ECOM_MODEL_STYLE_INVALID(422，_model_style_prompt_or_raise)。友好消息文本逐字对齐 BE。
  http.post(`${BASE}/api/v1/ecom-images/model`, async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown> & { apply_visible_label?: boolean };
    const ALLOWED = [
      "product_asset_ids", "model_asset_ids", "product_images_mode", "gender",
      "style_id", "custom_style", "extra_prompt", "aspect_ratio", "apply_visible_label", "source_asset_id"
    ];
    // extra="forbid" 镜像（对齐本文件既有写法：列出多余键，报错信息可操作）。
    const extra = Object.keys(body).filter((k) => !ALLOWED.includes(k));
    if (extra.length) return err(422, "VALIDATION_ERROR", `Extra inputs are not permitted: ${extra.join(",")}`);
    if (body.product_images_mode !== "multi_angle" && body.product_images_mode !== "multi_item")
      return err(422, "VALIDATION_ERROR", "product_images_mode 非法（multi_angle / multi_item）");
    const productIds = Array.isArray(body.product_asset_ids) ? (body.product_asset_ids as unknown[]) : [];
    const modelIds = Array.isArray(body.model_asset_ids) ? (body.model_asset_ids as unknown[]) : [];
    // extra_prompt / custom_style 反滥用上界 20000（BE _ECOM_MODEL_TEXT_LIMIT）→ 超限 422（非静默截断）。
    const overText = (v: unknown) => typeof v === "string" && v.length > 20000;
    if (overText(body.extra_prompt) || overText(body.custom_style))
      return err(422, "VALIDATION_ERROR", "自定义补充/自定义风格最多 20000 字符");
    const hasStyle = typeof body.style_id === "string" && body.style_id.length > 0;
    const hasCustom = typeof body.custom_style === "string" && body.custom_style.trim().length > 0;
    // 🔴 互斥：BE model_validator 是**拒绝**（非取其一），友好文案逐字对齐。
    if (hasStyle && hasCustom) return err(422, "VALIDATION_ERROR", "预设风格与自定义风格不能同时选择");
    if (productIds.length < 1) return err(422, "VALIDATION_ERROR", "请至少上传一张商品图");
    if (productIds.length + modelIds.length > 6) return err(422, "VALIDATION_ERROR", "商品图与模特图合计最多 6 张");
    // 未知 style_id → ECOM_MODEL_STYLE_INVALID（BE route 拼 prompt 时 _model_style_prompt_or_raise 抛）。
    const VALID_STYLES = ["studio_white", "lifestyle", "street", "office_commute", "resort_travel", "high_fashion"];
    if (hasStyle && !VALID_STYLES.includes(body.style_id as string))
      return err(422, "ECOM_MODEL_STYLE_INVALID", "Unknown AI model style.");
    const id = `mock-${++videoSeq}`;
    // 风格 key 用于 mock 产物 url：自定义 > 预设 > 默认（三档 top-down，避免嵌套三元）。
    let styleKey = "studio";
    if (hasCustom) styleKey = "custom";
    else if (hasStyle) styleKey = body.style_id as string;
    const url = `https://mock.local/model-${styleKey}.png`;
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

  // ── 电商详情图·强制复刻 (ECOM-REPLICATE-UI-0001 · FIX1) 真契约四端点 /replicate ──
  // POST /replicate（201）：创建 + 规划表（plan.outputs 全 planned），不扣费。请求体 product_info 为 dict。
  http.post(`${BASE}/api/v1/ecom-images/replicate`, async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as {
      output_mode?: string;
      reference_image_asset_ids?: string[];
      product_image_asset_ids?: string[];
      product_info?: unknown;
      selling_points?: string[];
      size?: string;
    };
    const mode = body.output_mode;
    if (mode !== "main" && mode !== "detail") return err(422, "ECOM_MODE_REQUIRED", "请选择生成模式：主图 / 详情页");
    const refs = body.reference_image_asset_ids ?? [];
    const products = body.product_image_asset_ids ?? [];
    // BE Field 约束（ECOM-REF-LIMIT-BE-0001）：参考图随模式（主图 ≤5 / 详情 ≤12）；商品图 ≤4；selling_points ≤8；product_info dict。
    const refMax = mode === "main" ? 5 : 12;
    if (refs.length < 1 || refs.length > refMax) return err(422, "ECOM_REF_REQUIRED", `请上传参考图（1–${refMax} 张）`);
    if (products.length < 1 || products.length > 4) return err(422, "ECOM_PRODUCT_REQUIRED", "请上传商品图（1–4 张）");
    if ((body.selling_points ?? []).length > 8) return err(422, "ECOM_POINTS_LIMIT", "核心卖点最多 8 条");
    if (typeof body.product_info !== "object" || body.product_info === null || Array.isArray(body.product_info)) {
      return err(422, "ECOM_PRODUCT_INFO_INVALID", "商品信息格式不正确");
    }
    const points = (body.selling_points ?? []).map((p) => String(p).trim()).filter(Boolean);
    const id = `ecomrep-${++ecomReplicateSeq}`;
    const size = mode === "main" ? "1024x1024" : "768x1024";
    const outputs = ecomBuildOutputs(id, mode, refs, products, points);
    // 承重·retry 演示：product_info.description 含 __FAIL__ → 首张(index 0)生成失败（→ partial_failed，可单张重试）。
    const infoText = JSON.stringify(body.product_info ?? {});
    ecomReplicateJobs.set(id, {
      job_id: id, status: "plan_ready", output_mode: mode, output_count: outputs.length,
      total_credits: outputs.length * ECOM_REPLICATE_RATE, credit_rate: ECOM_REPLICATE_RATE,
      requested_size: size, requested_aspect: ECOM_ASPECTS[size] ?? "1:1",
      outputs, _charged: false, _polls: 0, _failIndex: infoText.includes("__FAIL__") ? 0 : null
    });
    // BE 真状态码 201 Created。
    return HttpResponse.json(
      { data: ecomAcceptedResponse(ecomReplicateJobs.get(id)!), error: null, request_id: "mock-req" },
      { status: 201 }
    );
  }),
  // POST /replicate/{id}/confirm（202）：一次性扣费 + 幂等 → generating；返回 minimal（不含 plan/outputs）。
  http.post(`${BASE}/api/v1/ecom-images/replicate/:jobId/confirm`, ({ params }) => {
    const j = ecomReplicateJobs.get(String(params.jobId));
    if (!j) return err(404, "ECOM_REPLICATE_JOB_NOT_FOUND", "复刻任务不存在");
    if (!j._charged) {
      j._charged = true;
      j.status = "generating";
    }
    // BE 真状态码 202 Accepted。
    return HttpResponse.json(
      { data: { job_id: j.job_id, status: j.status, output_count: j.output_count, total_credits: j.total_credits }, error: null, request_id: "mock-req" },
      { status: 202 }
    );
  }),
  // GET /replicate/{id}（FIX2）：轮询用。generating 时第 2 次起逐张出图，全部终态转 completed/partial_failed。
  http.get(`${BASE}/api/v1/ecom-images/replicate/:jobId`, ({ params }) => {
    const j = ecomReplicateJobs.get(String(params.jobId));
    if (!j) return err(404, "ECOM_REPLICATE_JOB_NOT_FOUND", "复刻任务不存在");
    if (j.status === "generating") {
      j._polls += 1;
      if (j._polls >= 2) {
        for (const o of j.outputs) {
          if (o.status === "succeeded" || o.status === "failed") continue;
          if (j._failIndex === o.index) {
            o.status = "failed";
          } else {
            o.status = "succeeded";
            const url = `https://mock.local/ecom-replicate-${j.job_id}-${o.index}.png`;
            o.asset_id = `ecomimg-${j.job_id}-${o.index}`;
            o.download_url = `${url}?dl=1`; // FIX2 唯一图片 URL（预览+下载共用）
            const [w, h] = ECOM_ACTUAL_DIMS[o.requested_size] ?? [null, null];
            o.actual_width = w;
            o.actual_height = h;
          }
        }
        j.status = j.outputs.some((o) => o.status === "failed") ? "partial_failed" : "completed";
      }
    }
    return ok(ecomAcceptedResponse(j));
  }),
  // POST /replicate/{id}/outputs/{index}/retry（202）：失败单张回 planned + 整单回 generating；返回该单张。不重复扣费。
  http.post(`${BASE}/api/v1/ecom-images/replicate/:jobId/outputs/:outputIndex/retry`, ({ params }) => {
    const j = ecomReplicateJobs.get(String(params.jobId));
    if (!j) return err(404, "ECOM_REPLICATE_JOB_NOT_FOUND", "复刻任务不存在");
    const index = Number(params.outputIndex);
    const o = j.outputs.find((x) => x.index === index);
    if (!o) return err(404, "ECOM_REPLICATE_OUTPUT_NOT_FOUND", "该张不存在");
    if (o.status !== "failed") return err(409, "ECOM_REPLICATE_OUTPUT_NOT_RETRYABLE", "该张不可重试");
    // 回 planned + 整单 generating（_charged 不动 → 不重复扣）；清失败标记，下轮 GET 出图成功。
    o.status = "planned";
    o.asset_id = null;
    o.actual_width = null;
    o.actual_height = null;
    o.download_url = null;
    j._failIndex = null;
    j._polls = 0;
    j.status = "generating";
    // BE 真状态码 202 Accepted，返回该单张 EcomReplicatePlanOutput。
    return HttpResponse.json(
      {
        data: {
          id: o.id, index: o.index, theme: o.theme, reference_asset_id: o.reference_asset_id, product_asset_id: o.product_asset_id,
          requested_size: o.requested_size, requested_aspect: o.requested_aspect, status: o.status, prompt: o.prompt,
          asset_id: o.asset_id, download_url: o.download_url, actual_width: o.actual_width, actual_height: o.actual_height
        },
        error: null,
        request_id: "mock-req"
      },
      { status: 202 }
    );
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
      // provider 透出（缺省则 null）——镜像 COSYVOICE-CLONE-0001 合并后真形状，前端兼容 null。
      return { id: v.id, name: v.name, status: v.status, created_at: v.created_at, provider: v.provider ?? null };
    });
    return ok({ items, total: items.length });
  }),
  http.post(`${BASE}/api/v1/brand-voices`, async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as {
      name?: string;
      source_audio_asset_id?: string;
      consent_confirmed?: boolean;
      provider?: string; // 范围4：克隆通路 doubao/cosyvoice（COSYVOICE-CLONE-0001 合并后真契约）
    };
    // 真后端 extra=forbid + consent 校验：缺 source_audio_asset_id 或 consent_confirmed!==true → 422。
    if (!body.source_audio_asset_id || body.consent_confirmed !== true) {
      // 错误码逐字对齐后端 routes/brand_voices.py(BRAND_VOICE_CONSENT_REQUIRED)。
      return err(422, "BRAND_VOICE_CONSENT_REQUIRED", "需确认授权并提供音频资源");
    }
    // 请求侧 schema Literal["doubao","cosyvoice"]（缺省 doubao）：非法值 → 422（镜像 pydantic Literal）。
    if (body.provider !== undefined && !(body.provider in VOICE_CLONE_CANONICAL)) {
      return err(422, "VALIDATION_ERROR", "provider 非法（doubao / cosyvoice）");
    }
    // 存/返 canonical 长值（镜像 BE：alias 归一化 → _brand_voice_read 返 canonical）。
    const provider = VOICE_CLONE_CANONICAL[body.provider ?? "doubao"];
    // VIP 门禁：doubao 通路对无 entitlement 用户 → 403 VOICE_CLONE_PLAN_REQUIRED（防选了再撞的兜底；正常前端已置灰）。
    // entitlement 与 /me 同源（mockHuadingAccess = 平台租户 OR plan=huading，**不含 role==admin**）——新注册 free 用户在此被拦。
    // cosyvoice 免费档不受门禁——任何用户可建（含 0 余额新注册）。
    if (provider === "doubao-voice-clone" && !mockHuadingAccess(resolveMockState())) {
      return err(403, "VOICE_CLONE_PLAN_REQUIRED", "Voice clone (doubao) requires the huading plan.");
    }
    const id = `bv-${++brandVoiceSeq}`;
    brandVoices.set(id, { id, name: body.name || "未命名品牌音色", status: "processing", created_at: new Date(0).toISOString(), _polls: 0, provider });
    return ok({ id, name: body.name || "未命名品牌音色", status: "processing", created_at: new Date(0).toISOString(), provider });
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

  // ── 数据看板 (ANALYTICS-UI-0001 / PROD-P0-ANALYTICS-TENANT-LEAK-UI-0001) ── /api/v1/admin/analytics/*。
  // 门禁与数据 scope 均按 entitlement 派生（无 analytics_view → 403；无 analytics_platform → 只返自己单租户）。
  // 场景切换用 hd_mock_platform / hd_mock_plan（默认平台租户 → 全站正常，零回归）。
  ...analyticsHandlers(),

  // ── 管理员后台 (ADMIN-CONSOLE-UI-0001) ── /api/v1/admin/console/*，门禁与数据全走 resolveMockState 单一源。
  ...adminConsoleHandlers(),

  // ── 华鼎AI智脑 (AIBRAIN-UI-0001) ── /api/v1/aibrain/*，会话/消息/钱包/充值 mock；正确拒绝非法档位/余额不足/超上限/未知会话。
  ...aibrainHandlers()
];
