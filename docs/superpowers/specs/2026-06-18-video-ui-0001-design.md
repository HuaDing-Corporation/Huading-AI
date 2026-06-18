# VIDEO-UI-0001 · 数字人口播傻瓜化前端 — 设计 Spec

- **编号**：VIDEO-UI-0001
- **日期**：2026-06-18
- **分支**：`feature/video-ui`（从 `origin/develop` e4892b83 切，已含 #24 前端骨架）
- **合并**：与后端 `VIDEO-PIPELINE-0001` 同批合 `develop`
- **真源（seam）**：《数字人口播MVP-实施方案与API契约冻结-20260618》§8 SSE / §9 端点 / §10 错误。任一方改契约先改该文件并知会对方。
- **配套真源**：《前端骨架方案-数字人口播MVP-20260613》《设计系统 v1.0》。

> 本文件是 brainstorming 定稿的实现依据。下一步 writing-plans 出实施计划，再按 `UI UX Pro Max → Frontend-design → Code Simplifier → Webapp Testing/Playwright → Chrome DevTools → Code Review(Codex B)` 落地。

---

## 1. 目标

在已合入 develop 的前端骨架（feature-based 目录 / React Query / 唯一数据层 `lib/api` / `useTaskProgress` / Radix 玻璃皮 / 客户端鉴权门）上，落地**傻瓜化口播输入页 + 成片详情/播放**，按 §9 冻结契约对接，达成端到端验收闭环：

**输主题 → AI 生成可改文案 → 选形象(上传/预设) + 音色 → 生成 → 看 SSE 进度 → 内嵌播放/下载/seek → 刷新留存 → 失败态有兜底**。

本质：一次 **M2 → 冻结契约的数据层迁移** + **avatar_talk 傻瓜化 UI 落地**。数据层 `lib/api` 是唯一改动收口点。

## 2. 范围

**做**
- 工作台 `/`（同页）：`NewVideoForm`（主题/卖点 → `ScriptReview` 可改文案 → `ImagePicker` 上传/预设 → `VoicePicker` 音色 → `MoreSettings` 折叠[语速/尺寸默认9:16锁/字幕默认开不可关] → 金底生成按钮）；`TaskList`/`TaskCard`（状态徽+进度+缩略图，SSE 实时，三态）。
- 成片详情 `/videos/[id]`：`VideoDetail` + `VideoPlayer`（seek/下载/onError 刷 URL）+ `SubtitlePreview`（仅展示 `script` 文本，字幕已烧入视频）。
- 顶栏 `QuotaBadge`：接真 `GET /quota`。
- 新 hooks（`lib/api` 唯一数据层）：`useScriptGenerate`、`useVoices`、`useAvatarPresets`、`useQuota`（接真）。
- SSE 简化：`useTaskProgress` 消除 M2 双映射（保留旧大写帧兜底），`done` 触发重取 `GET /videos/{id}` 拿权威预签名 URL。
- 失败/超时 UI（Seedance 卡死无失败态的坑必须补）。
- MSW Mock 层（并行期，dev/test）；文案集中管理 `lib/copy.ts`。

**不做**
- 封面/发布/批量/品牌/电商图入口（侧栏置灰预留，不建空壳）。
- react-bits 营销页（核心控制台动效克制）。
- "自己写文案"开关（只 AI 文案可改/重写）。
- i18n 框架（文案集中不硬编码即可，V4 再上 next-intl）。
- **Auth 不迁移**（见 §11）。
- Seedance UI（被 avatar_talk 替换为主入口；后端 Seedance 停车保留不删，契约 §12）。

## 3. 已确认关键决策

1. **响应封套保持 M2** `{data, error, request_id}`：后端 `ok()` 统一包裹，§9 schema 是内层 `data`。`apiFetch` 已正确解包 `.data`，**不改 client**。（契约已更正：后端保持封套，非裸 JSON。）
2. **并行期对接 Mock**：缺失的 §9 端点用 MSW 网络层拦截；`lib/api` 保持纯净零 mock 分支。真联调在前后端都合 develop 后做一次。
3. **avatar_talk 替换 Seedance UI**：新 `NewVideoForm` 取代旧 `NewVideoCard`。POST /videos 收敛为 avatar_talk 单形态（无 `video_mode`）。「不破 M2 回归」= 登录→生成→SSE→播放**管道**仍通（用新 avatar_talk 流验证），非保留 Seedance UI。
4. **Auth 两端都保持 M2**：login 仍带 `tenant_slug`、读 `token.tenant_id`；`/me` 仍是 `{tenant,user,permissions}`；`useMe`/`top-bar` 照 M2 消费。契约 §9.1 已标注 auth=M2 现状。**本包不动 login**。

## 4. 数据层契约映射（`lib/api` — 核心）

> 铁律：UI 组件**绝不直接 fetch**；契约映射只在此。展示组件纯 props，数据由容器经 hooks 注入。

### 4.1 类型（`types.ts` 重写，对齐 §9）

替换/新增（删除 M2 的 `title/prompt/mode/duration_sec/error/video_mode/image_key/n_scenes/pipeline`）：

```ts
export type VideoStatus = "queued" | "running" | "done" | "failed";

// GET /videos → {items: VideoListItem[], total}
export interface VideoListItem {
  id: string; status: VideoStatus; progress: number; // 0..100
  topic: string; thumbnail_url?: string | null; created_at: string;
}
export interface VideoListResponse { items: VideoListItem[]; total: number; }

// GET /videos/{id}
export interface VideoDetail {
  id: string; status: VideoStatus; progress: number;
  topic: string; script: string; voice_id: string;
  aspect_ratio: string; subtitle_enabled: boolean;
  playback_url?: string | null; download_url?: string | null; thumbnail_url?: string | null;
  duration_ms?: number | null; error_code?: string | null; error_message?: string | null;
  created_at: string; finished_at?: string | null;
}

// POST /videos → 202 {id, status:"queued"}
export interface CreateVideoRequest {
  topic: string;                 // 必填 ≤500
  script?: string;               // 可选；缺则后端 DeepSeek 生成（前端流程会带）
  voice_id: string;              // 必填
  avatar_asset_id: string;       // 必填（上传或预设产出的 asset_id；契约把 image_key 冻结为此）
  speed?: number;                // 默认 1.0
  aspect_ratio?: string;         // 默认 "9:16"
  subtitle_enabled?: boolean;    // 默认 true
}
export interface VideoAccepted { id: string; status: string; } // 注意：M2 是 task_id → 改 id

// GET /voices → {items: Voice[], total}
export interface Voice {
  id: string; provider: string; voice_code: string; display_name: string;
  gender: string | null; language: string | null; sample_url?: string | null; // 契约 §9.2: gender/language 为存在字段(值可空)
}
// GET /avatars/presets → {items: AvatarPreset[], total}（可为空 → 只走上传）
export interface AvatarPreset { asset_id: string; display_name: string; thumbnail_url: string; }

// POST /scripts/generate {topic} → {script}
export interface ScriptGenerateResponse { script: string; }

// POST /uploads/images (multipart) → 201
export interface UploadImageResponse {
  asset_id: string; type: "avatar_image"; status: "ready"; thumbnail_url?: string | null;
}

// GET /quota → credits，2 位小数
export interface Quota { total: number; used: number; reserved: number; remaining: number; }

// SSE 新枚举帧（§8）
export interface VideoEvent {
  status?: VideoStatus | string;     // 权威字段：queued|running|done|failed（兼容旧大写）
  progress?: number;                 // 0..100 int（旧帧是 0..1，兜底换算）
  step?: string | null;              // tts|avatar|subtitle|compose|upload
  playback_url?: string | null; download_url?: string | null; thumbnail_url?: string | null;
  error_code?: string | null; error_message?: string | null;
  // 旧帧兜底字段（保留容错）
  task_id?: string; stage?: string | null; error?: string | null; timeout_seconds?: number;
}
```

`ApiResponse<T> = {data: T|null, error:{code,message,request_id,detail?,details?}|null, request_id:string|null}` —— **保持不变**。（契约 §10 错误信封为 `details?`；`detail?` 是后端 `ErrorDetail` 实际并存的兼容别名，`apiFetch` 读 `err.detail`，保留。）

### 4.2 api 函数

| 模块 | 函数 | 端点 |
|---|---|---|
| `videos.ts`（改） | `createVideo(CreateVideoRequest)→VideoAccepted` | POST `/api/v1/videos` |
| | `getVideo(id)→VideoDetail` | GET `/api/v1/videos/{id}` |
| | `listVideos()→VideoListItem[]` | GET `/api/v1/videos`（返 `res.items`） |
| | `streamVideoEvents(id, onMessage, signal)` | GET `/api/v1/videos/{id}/events`（SSE，保持现有 fetch-stream 解析） |
| `scripts.ts`（新） | `generateScript(topic)→ScriptGenerateResponse` | POST `/api/v1/scripts/generate` |
| `voices.ts`（新） | `listVoices()→Voice[]` | GET `/api/v1/voices` |
| `avatars.ts`（新） | `listAvatarPresets()→AvatarPreset[]` | GET `/api/v1/avatars/presets` |
| `quota.ts`（新） | `getQuota()→Quota` | GET `/api/v1/quota` |
| `uploads.ts`（改） | `uploadImage(file)→UploadImageResponse` | POST `/api/v1/uploads/images`（multipart，返 `asset_id`） |

### 4.3 hooks（`hooks.ts`）

新：`useScriptGenerate`（mutation）、`useVoices`、`useAvatarPresets`、`useQuota`（接真，返 `Quota`）。
改：`useUploadImage`（返 `asset_id`）。
复用：`useVideos`/`useVideo`/`useCreateVideo`/`useTaskProgress`/`useMe`，以及既有 **`useVideoTasks`**（`lib/videos/tasks-context.tsx` 的 context hook，TaskList 的实时任务状态来源）。所有查询 `enabled: !!session`（gate on token）。`useCreateVideo` onSuccess invalidate 列表。`keys.ts` 增 `voicesKey`/`avatarPresetsKey`。

## 5. SSE 升级与进度（消双映射 + 保留容错）

- `progress-mapping.ts`：`eventToProgress` 直接吃**新枚举**（小写 status，`progress` 当作 0..100 int，`step` → 中文标签）。**保留旧帧兜底**：若 status 是大写（SUCCESS/FAILURE/PROGRESS…）或 `progress ≤ 1` 的小数，走旧映射换算。`stage==="sse_timeout"` 仍当 keep-alive 忽略。
- **实时循环主消费方 = `lib/videos/tasks-context.tsx`**（`applyEvent`/`pollFallback`/`subscribe`/`createAndTrack` 直接调 `eventToProgress`/`fromVideoRead`/`getVideo`/`TERMINAL`）——这是必须改的生产路径；`lib/sse/use-task-progress.ts` 是次要/重复路径（仅其自身测试消费），两者经 `progress-mapping.ts` 共同受益。
- SSE 为主 + React Query `refetchInterval` 轮询兜底；`done` → 重取 `GET /videos/{id}` 拿权威 `playback_url/download_url/thumbnail_url` → 停 SSE+轮询；`failed`/超时 → 失败态 + 重试入口。
- `fromVideoRead` 改吃 `VideoDetail | VideoListItem` 联合，**对窄类型容错**：列表项无 `playback_url/download_url/duration_ms`（权威 URL 仍由 done-reconcile 补），`durationSec = duration_ms!=null ? duration_ms/1000 : null`，error 取 `error_message`，topic 取代 `title||prompt`。
- step→进度权重映射（展示用，§4 进度建议）：queued0/script10/tts20/avatar20-85/subtitle90/compose95/upload98/done100；映射只为标签，权威进度仍以帧 `progress` 为准。

## 6. 组件设计（feature-based，展示纯 props + 容器注入）

> 复用硬规则：同类 UI 结构 >2 次必抽象，禁复制粘贴。

### 6.1 复用原子
- `SelectableOption`（`ui/`）：可选卡/行，承载 ImagePicker 预设网格 与 VoicePicker 选项；props `selected/onSelect/disabled/children`。**选中态与既有 `ui/chip.tsx` 完全同构**（`border-line-sel bg-chip-sel text-gold-deep`）——必须**复用同一份选中态样式**（抽共享 class/cva 或基于 Chip variants 扩展），并消除 `new-video-card.tsx` 内联的第 3 份复制；**不得**让 Chip 与 SelectableOption 并存为两个重复选择原子（违反 §6 复用规则）。
- 复用既有 `ui/`：Glass/Button/Input/Card/Chip/StatusBadge/Progress/Avatar/Logo + Radix 封装 Dialog/Select/Popover/Tabs/Tooltip。

### 6.2 workbench/
| 组件 | 职责 | 关键 props | 边界 |
|---|---|---|---|
| `NewVideoForm`（容器） | 编排四件套 + 更多设置 + 下单 | — | 唯一调 hooks 处；组装 `CreateVideoRequest` |
| `ScriptReview` | 展示 AI 文案 + 可编辑 + 重写 | `script/onChange/onRegenerate/loading` | 不发起生成 |
| `ImagePicker` | 上传（校验）或选预设 → 产 `avatar_asset_id` | `value/onChange/presets/uploading/onUpload` | 只产 asset_id，不下单 |
| `VoicePicker` | 选音色（可试听 sample_url） | `voices/value/onChange` | 纯展示 |
| `MoreSettings` | 折叠：语速滑杆 / 尺寸(9:16 锁) / 字幕(开锁) | `speed/onSpeedChange` | 尺寸字幕只读展示锁定态 |

- **锁定态 token**：尺寸/字幕的只读锁定外观用既有禁用语义（`bg-queue-bg` / `text-ink-faint`）+ 锁图标，不用临时 opacity 或未定义 disabled token。
- **ScriptReview 重写路径**：`onRegenerate` 重新调 `useScriptGenerate(topic)` 覆盖可编辑缓冲；用户手改文本保留进 `CreateVideoRequest.script`，除非再次重写。`topic` 取表单当前主题。

### 6.3 tasks/
- `TaskList`（容器消费既有 `useVideoTasks`，来自 `lib/videos/tasks-context.tsx`，提供 `tasks/createAndTrack/refreshTask`）+ `TaskCard`（纯展示三态：排队=进度条、进行=进度条+step、失败=`error_message`+重试、完成=缩略图+打开详情）。`TaskCard` 纯 props + 发"打开详情/重试"事件。**失败态 token**：`bg-error-bg`/`text-error-fg`（已在 globals.css）；缩略图失败可加 `--shadow-thumb-failed`（实现期补 token + tailwind boxShadow，error 色调变体）。

### 6.4 video/
| 组件 | 职责 | 关键 props |
|---|---|---|
| `VideoDetail`（容器） | `useVideo(id)` 拉详情，组织播放/字幕/元信息 | `id` |
| `VideoPlayer` | 播放/seek/下载/onError 刷 URL | `playbackUrl/downloadUrl/poster/onUrlExpired` |
| `SubtitlePreview` | 仅展示 `script` 文本（字幕已烧入） | `script` |

### 6.5 layout/
- `QuotaBadge`：消费 `useQuota`，展示 `remaining/total`。**用既有中性玻璃 pill**（`bg-glass-soft` + `text-ink-soft`，已 token 驱动且 AA 安全）——顶栏元信息 pill 不用重量级金底（无轻量金 pill token，避免临时造色）；无数据时不渲染假数字。

## 7. 路由与页面
- `app/(app)/page.tsx` → 工作台（`NewVideoForm` + `TaskList`），替换旧 Seedance 版。
- `app/(app)/videos/[id]/page.tsx`（新）→ `VideoDetail`，独立路由=可刷新直达/分享。
- 鉴权门沿用 `(app)/layout.tsx` 客户端门（M2 现状，server gate 待 #M2-AUTH-COOKIE）。

## 8. 文案集中管理
- `lib/copy.ts`：集中所有中文 UI 文案（placeholder/按钮/状态标签/错误友好语/空态）。组件 import，不内联硬编码。为 V4 i18n 预留结构（按 feature 分组的对象）。

## 9. Mock 策略（MSW，并行期）
- **MSW**：dev 用 service worker、vitest 用 `setupServer`、Playwright 复用同套 handlers（或 Playwright route）。`lib/api` 零 mock 分支。
- 开关：`NEXT_PUBLIC_USE_MOCK=1` 启用；真后端就绪时关并指向真 API（`NEXT_PUBLIC_API_BASE_URL`）。
- handlers 覆盖：`/auth/login`、`/auth/me`、`/quota`、`/voices`、`/avatars/presets`、`/scripts/generate`、`/uploads/images`、POST/GET `/videos`、GET `/videos/{id}`、SSE `/videos/{id}/events`。
- **Mock 响应连封套返** `{data:…, error:null, request_id:"mock-…"}`；SSE 发**新枚举渐进帧**（queued→running 多帧带 step/progress→done 带 url；另备 failed 序列）。
- 仅 dev/test 依赖（不进生产 bundle）。

## 10. 错误/失败/超时处理
- `ApiError`（现有）：401 清 session 跳 login；网络错误友好提示。
- 下单 403 `tenant_quota_exceeded` → 明确"额度不足"提示，不入队。
- 上传 413 `payload_too_large` → "图片过大"提示。
- 任务 `failed`（`error_code`/`error_message`）→ TaskCard/详情失败态 + **重试入口**（重提同参 → 新任务）。
- **客户端自超时（关键，必须独立于后端 `failed` 事件）**：跟踪每个在途任务「上次进度推进时间」。运行期满足任一即强制失败态 + 重试：(a) 停滞窗口 `STALL_MS`（默认 120s）内 `progress` 无推进且无终态帧；(b) 自 `queued` 起总时长超硬上限 `HARD_CAP_MS`（默认 15min）。帧若带 `timeout_seconds` 则优先采用。常量集中可配。**即使后端永不发 `failed` 也要触发**——这正是 Seedance 卡死坑的根因。
- SSE 断 → 轮询兜底；轮询也失败 → 失败态。脱敏：只展示 `error_message`，不泄堆栈。

## 11. 安全 / 鉴权
- 所有请求带 `Bearer`（现有 `authHeaders`）；查询 `enabled:!!session`；401→清 session→/login。
- 租户由 JWT 推导（契约 §9）；现有 `X-Tenant-ID` 头后端忽略，保留无害（M2 兼容）。
- **Auth 两端保持 M2，不迁移**（§3.4）：login 带 `tenant_slug`，`TokenResponse` 含 `tenant_id/user_id/role`，`/me`→`{tenant,user,permissions}`，`useMe`/`top-bar` 照旧。本包不动 login。

## 12. 测试与验收（证据贴回执）
1. `pnpm lint` 无告警、`tsc --noEmit` 过、`pnpm build` ✓。
2. `pnpm test`（vitest）全绿（含数据层映射、SSE 新/旧帧、组件）。
3. grep 证 UI 组件无裸 `fetch`（后端访问全在 `lib/api`）。
4. grep 证无硬编码色值；金底深墨字 AA。
5. 复用自查：无 >2 次复制粘贴的同类结构。
6. Playwright 全流程对 MSW 跑（截图/录制）：登录门 → 输主题 → 生成文案可改 → 上传形象 → 选音色 → 生成 → SSE 进度推进 → done → 内嵌播放(currentTime>0)+seek+下载 → 刷新历史留存 → 失败态 → 移动端无溢出。
7. Chrome DevTools：Console 0 报错；Network 关键接口 200/201/202（SSE `/events` 为 200 流式）。
8. 不破 M2 回归：登录→生成→SSE→播放管道现场过（用 avatar_talk 流）。
9. CI 三绿（push 后）。

> 真后端 e2e（真 OmniHuman 成片→播放）在 VIDEO-PIPELINE-0001 与本包都合 develop 后做一次联调；回执注明哪些验收对真后端、哪些对 Mock。

## 13. 迁移点 / 不破 M2（additive）
- `VideoAccepted.task_id → id`：`tasks-context.createAndTrack` 用 `accepted.id`。
- `fromVideoRead` 与 `progress-mapping` 改吃新 schema。
- POST /videos body 由 seedance 形态 → avatar_talk 形态。
- 上传端点 `/uploads → /uploads/images`，返回 `key → asset_id`。
- SSE 旧帧→新枚举：保留旧帧兜底，前向兼容；现场回归 M2 管道。
- `Quota {used,limit,resetAt} → {total,used,reserved,remaining}`：删 `lib/api/hooks.ts` 内本地 `interface Quota`，移到 `types.ts`；`components/layout/quota-badge.tsx` 由 `used/limit` 改 `remaining/total`；`useQuota` 返回类型由占位 `Quota|null` → 真 `Quota`。
- **测试夹具迁移（否则 `pnpm test` 红）**：`lib/api/hooks.test.tsx`（createVideo mock `{task_id}`→`{id}`）、`lib/sse/progress-mapping.test.ts`（`fromVideoRead` 入参 title/prompt/mode → topic 等新 schema）、`lib/sse/use-task-progress.test.tsx`（getVideo mock 改新 schema）。
- `VideoListResponse {items, next_cursor} → {items, total}`：无消费者读 next_cursor；`total` 可用于列表计数或不引入。
- 旧 `lib/mock.ts`（占位静态数据）按需清理/替换为 MSW；旧 `NewVideoCard` 删除（被 NewVideoForm 替换）。

## 14. 范围外 / 风险
- **Auth 契约分歧已消除**：两端都不按 §9.1 迁移 auth，保持 M2。无登录断裂风险。
- 真后端 §9 端点由 Codex A 并行建，真实 shape 在集成时验证；本包以契约为准 + MSW。
- OmniHuman 真出片 e2e 需公网对象存储（即梦拉不到 localhost MinIO）；属后端/联调范畴，不阻塞本包 UI。
- 字幕样式（位置/字号/描边）是**后端 burn-in/渲染管线**职责（成片已烧入字幕），**前端不需要字幕 token**；`SubtitlePreview` 仅展示纯 `script` 文本。（契约 §13「字幕样式走设计 token」指渲染端规范，非前端组件。）

## 15. 交付物清单
- `components/workbench/`：NewVideoForm、ImagePicker、ScriptReview、VoicePicker、MoreSettings。
- `components/tasks/`：TaskList、TaskCard（三态）。
- `components/video/`：VideoDetail、VideoPlayer、SubtitlePreview。
- `components/ui/`：SelectableOption（复用原子）。
- `components/layout/`：QuotaBadge（接真）。
- `lib/api/`：scripts/voices/avatars/quota（新）+ videos/uploads/types/hooks/keys（改）。
- `lib/copy.ts`（文案集中）；`lib/sse/*`（简化）。
- `app/(app)/page.tsx`（工作台）、`app/(app)/videos/[id]/page.tsx`（详情）。
- MSW handlers（dev/test）。
- Playwright 用例 + vitest 用例。

## 16. backlog（非阻塞，动到相关文件就带）
- #24 遗留 2 个 P2：Dialog overlay token 化、`owner`→`admin` 测试夹具（`lib/api/client.test.ts:7`，Role 无 `owner`）。本包动到相关文件就一并清，否则留 backlog。
- `components/ui/avatar.tsx` 注释 "white initials" 过时（代码已用 `text-ink` 符合 DS §5），动到就顺手改注释。
