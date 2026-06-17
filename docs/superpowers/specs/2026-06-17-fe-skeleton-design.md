# #FE-SKELETON-0001 — 前端骨架落地

## Context

M2 console 已能跑（登录 → 工作台 → 新建 → SSE 进度 → 成片播放）。本任务把它升级成一套可长期演进的前端骨架：feature-based 目录、唯一数据层（React Query hooks，UI 禁裸 fetch）、SSE 进度抽成干净 hook、Radix headless 原语底座、设计 token 驱动、route groups + 鉴权门。

**原则：additive、不破现有 M2 console。** 迁移策略 = 骨架优先、渐进迁移（先立新骨架，再把现有文件归位并改走 hooks，每步过 M2 回归）。

依据：《前端骨架方案-数字人口播MVP-20260613》+《华鼎设计系统》+ 立项书 §5「前端规范」。分支 `feature/fe-skeleton`（已从最新 develop 建好，自带 #M2-AUTH-FIX）。

## 两处落地修正（设计已确认）

1. **middleware 鉴权门的限制**：会话 token 存于 localStorage（客户端）。Next.js `middleware.ts` 跑在服务端/edge，**读不到 localStorage**，因此本轮无法做真正的服务端鉴权门。鉴权门主力放在 `(app)/layout.tsx`（客户端，读 `useAuth().session`，未登录 → `/login`，复用现 `page.tsx` 已验证逻辑）；`middleware.ts` 本轮只做薄壳（软跳转 / 占位 + 注释）。**真正的服务端鉴权门留给 M3 #M2-AUTH-COOKIE（httpOnly cookie）。**
2. **authStore 不是 Zustand**：现 `lib/auth/store.ts` 是自定义、已测的单例（无 zustand 依赖）。任务措辞「保留现有 Zustand authStore」与实现不符；本轮**保留现有自定义单例**，不强行换 Zustand。

## 目录结构（目标）

```
src/
  app/
    (auth)/login/page.tsx          移自 app/login/page.tsx
    (app)/layout.tsx               共享 shell：TopBar(含 QuotaBadge) + Sidebar + 客户端鉴权门
    (app)/page.tsx                 工作台，移自 app/page.tsx
    layout.tsx                     root：Providers（QueryProvider + AuthProvider + VideoTasksProvider）
    globals.css                    设计 token（已就位）
    middleware.ts                  薄壳（见修正 1）
  components/
    ui/        现有原子组件 + Radix 封装（玻璃皮）
    layout/    TopBar, Sidebar, QuotaBadge（移自 components/console/{top-bar,sidebar}）
    workbench/ NewVideoCard（移自 components/console/new-video-card）
    tasks/     TaskList（移自 components/console/task-list）
    video/     VideoPlayer / 预览（从 task-list 抽离）
  lib/
    api/       client.ts + hooks（useVideos/useVideo/useCreateVideo/useUploadImage/useQuota/useMe）
    sse/       useTaskProgress（包装现有"双映射 reconcile" + 轮询兜底）
    auth/      store.ts（保留自定义单例）+ auth-context.tsx
    query/     queryClient + QueryProvider
```

## 数据层（唯一 lib/api，UI 禁裸 fetch — 铁律）

- 引 **@tanstack/react-query v5**；root `layout.tsx` 包 `QueryProvider`（client component，单例 `QueryClient`）。
- hooks 全部封装现有 `apiFetch`（保留 ApiResponse 解包 + 鉴权头）：
  - `useVideos()` → `GET /api/v1/videos`（list）
  - `useVideo(id)` → `GET /api/v1/videos/{id}`
  - `useCreateVideo()` → `POST /api/v1/videos`（mutation，成功后 invalidate videos）
  - `useUploadImage()` → uploads 端点（mutation）
  - `useMe()` → `GET /api/v1/auth/me`
  - `useQuota()` → **provisional**：后端暂无 quota 端点。先定类型 `{ used: number; limit: number; resetAt: string }` + 占位实现（返回禁用/空态），代码标 `TODO(#后端 quota 端点)`。QuotaBadge 消费它，端点就绪后只改 hook 实现。
- **`useTaskProgress(taskId)`**：把 `lib/videos/tasks-context.tsx` 里已验证的 SSE（`streamVideoEvents`）+ 轮询兜底 + 双映射 reconcile（SSE 大写状态 ↔ VideoRead）逻辑搬进 `lib/sse`，对外是干净 hook。`tasks-context` 退化为薄封装以保 M2 不破；组件逐个改走 hook。
- 验收：`grep -rE "fetch\(|axios"` 在 `components/` 下无命中（除注释/类型）。

## 路由 & 鉴权门

- route groups：`(auth)`（login，register 预留目录）、`(app)`（共享 shell）。
- `(app)/layout.tsx`：客户端组件，`const { session, ready } = useAuth()`；`ready && !session` → `router.replace("/login")`；`!ready || !session` 时渲染 `aria-busy` 占位（复用现 `page.tsx` 行为，避免闪烁 + 避免未登录发认证请求）。
- `middleware.ts`：薄壳——可对 `(app)` 路径做基于非敏感存在性 cookie 的软跳转，或先 `NextResponse.next()` + 注释说明真鉴权门待 #M2-AUTH-COOKIE。不依赖 localStorage。
- 侧栏 MVP 只亮 工作台 / 历史 / 账户，其余置灰（沿用现 Sidebar）。

## Radix headless 原语

引 `@radix-ui/react-dialog`、`react-select`、`react-popover`、`react-tabs`、`react-tooltip`；在 `components/ui` 各包一层，只取行为、套玻璃皮（沿用现 `glass.tsx`/`button.tsx` 风格，token 驱动）。本轮先落底座；现有手写交互按需替换，不为替换而替换（YAGNI）。

## 设计 token

现 `globals.css` 已是 `:root` CSS 变量 + 映射进 Tailwind theme（组件已用 `text-ink-soft`/`bg-glass-soft`/`rounded-field` 等语义类）。本轮只做审查：确保新增组件不写字面色值，必要时补缺失 token。

## 迁移顺序（每步过 M2 回归 + 提交）

1. 依赖 + `lib/query`（QueryProvider）+ root layout 接入。
2. `lib/api` hooks（含 provisional useQuota）。
3. `lib/sse` `useTaskProgress`（搬 reconcile 逻辑），`tasks-context` 改薄封装。
4. 目录归位：`components/{layout,workbench,tasks,video}`（移文件 + 改 import）。
5. route groups `(auth)/(app)` + `(app)/layout.tsx` 客户端鉴权门。
6. `middleware.ts` 薄壳。
7. Radix 原语底座（`components/ui` 封装）。
8. 复用去重（Code Simplifier）：同类 UI 结构 >2 次抽象。

## 依赖（回执说明）

- 新增：`@tanstack/react-query@^5`、`@radix-ui/react-{dialog,select,popover,tabs,tooltip}`。
- 保留：自定义 authStore（非 Zustand）、现有 `apiFetch`、现有设计 token。
- 不破现有契约（如破，回执列出）。

## 测试 & 验收门

1. **vitest**（已搭好）：`useTaskProgress`（SSE 事件→状态映射、轮询兜底、terminal 终止）、关键 hooks（useVideos/useCreateVideo 的 query/mutation 行为，mock apiFetch）单测。
2. **不破 M2（Playwright，复用本次现场栈）**：登录 → 工作台 → 新建 → SSE 进度 → 成片播放，截图为证。
3. `lint`（next lint）+ `typecheck`（tsc/`next build`）+ `build` 全绿。
4. **UI 不直接 fetch**：`grep` 证明 `components/` 内无裸 fetch/axios，全走 `lib/api`。
5. 复用规则：无 >2 次重复 UI 结构（Code Simplifier 过）。
6. Chrome DevTools：Console 无报错，Network 关键请求 200。

## 范围外（后续任务，依赖后端业务端点）

数字人口播傻瓜化输入页完整业务流（依赖 backend `scripts/generate`、`uploads/images`、avatar 模式 videos）、react-bits 营销页、前端 SSE 新枚举对齐（等 backend #M2-SSE-ALIGN）、真正的服务端 middleware 鉴权门（等 #M2-AUTH-COOKIE）、quota 端点对接。
