# FE Skeleton (#FE-SKELETON-0001) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a long-lived frontend skeleton (React Query data layer, SSE progress hook, feature-based folders, route groups + client auth gate, Radix primitives) and progressively migrate the working M2 console onto it without breaking it.

**Architecture:** Skeleton-first, progressive migration. Add React Query as the single data layer (UI never fetches directly); wrap the proven SSE+poll reconcile logic as `useTaskProgress`; relocate existing console components into `components/{layout,workbench,tasks,video}`; move pages into `(auth)`/`(app)` route groups with a client-side auth gate in `(app)/layout.tsx` (true server gate deferred to #M2-AUTH-COOKIE since the token lives in localStorage). Each task is an independently testable increment; M2 stays green throughout.

**Tech Stack:** Next.js 15 App Router · React 19 · TypeScript · @tanstack/react-query v5 · @radix-ui/react-* · Vitest + jsdom + Testing Library (already bootstrapped).

---

## File Structure

- `frontend/src/lib/query/client.ts` — **Create.** `QueryClient` factory + default options.
- `frontend/src/lib/query/query-provider.tsx` — **Create.** Client `QueryProvider`.
- `frontend/src/lib/api/keys.ts` — **Create.** Query-key factories.
- `frontend/src/lib/api/hooks.ts` — **Create.** `useVideos/useVideo/useCreateVideo/useUploadImage/useMe/useQuota`.
- `frontend/src/lib/api/hooks.test.tsx` — **Create.** Hook tests (mock api modules + useAuth).
- `frontend/src/lib/sse/progress-mapping.ts` — **Create.** Pure helpers extracted from tasks-context (`mapSseStatus`, `labelFor`, `fromVideoRead`, `TERMINAL`).
- `frontend/src/lib/sse/progress-mapping.test.ts` — **Create.** Mapping unit tests.
- `frontend/src/lib/sse/use-task-progress.ts` — **Create.** Single-task SSE+poll hook.
- `frontend/src/lib/sse/use-task-progress.test.tsx` — **Create.** Hook test.
- `frontend/src/lib/videos/tasks-context.tsx` — **Modify.** Import shared helpers from `lib/sse` (no behavior change).
- `frontend/src/app/layout.tsx` — **Modify.** Wrap with `QueryProvider`.
- `frontend/src/components/{layout,workbench,tasks,video}/*` — **Create (git mv).** Relocate console components.
- `frontend/src/app/(auth)/login/page.tsx`, `frontend/src/app/(app)/layout.tsx`, `frontend/src/app/(app)/page.tsx` — **Create (git mv + new layout).** Route groups + client auth gate.
- `frontend/src/middleware.ts` — **Create.** Thin shell.
- `frontend/src/components/ui/{dialog,select,popover,tabs,tooltip}.tsx` — **Create.** Radix wrappers.
- `frontend/src/components/layout/quota-badge.tsx` — **Create.** Consumes `useQuota`.

All commands run from `frontend/` unless noted. Package manager is **pnpm** (workspace `@huading/frontend`); add deps with `pnpm --filter @huading/frontend add ...` from repo root.

---

## Task 1: Dependencies + React Query provider

**Files:**
- Modify: `frontend/package.json` (via pnpm)
- Create: `frontend/src/lib/query/client.ts`, `frontend/src/lib/query/query-provider.tsx`
- Modify: `frontend/src/app/layout.tsx`

- [ ] **Step 1: Add dependencies**

Run (from repo root `C:\Users\Administrator\Desktop\华鼎-worktrees\claude-code`):
```bash
pnpm --filter @huading/frontend add @tanstack/react-query@^5.62.0
pnpm --filter @huading/frontend add @radix-ui/react-dialog@^1.1.4 @radix-ui/react-select@^2.1.4 @radix-ui/react-popover@^1.1.4 @radix-ui/react-tabs@^1.1.2 @radix-ui/react-tooltip@^1.1.6
```
Expected: installs without error; `package.json` dependencies updated. If the registry is unreachable: `pnpm config set registry https://registry.npmmirror.com` then retry.

- [ ] **Step 2: Create the QueryClient factory**

Create `frontend/src/lib/query/client.ts`:
```ts
import { QueryClient } from "@tanstack/react-query";

// One client per browser session. Conservative defaults: short staleness, one
// retry, no refetch-on-focus (the console drives freshness via SSE + invalidate).
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false }
    }
  });
}
```

- [ ] **Step 3: Create the QueryProvider**

Create `frontend/src/lib/query/query-provider.tsx`:
```tsx
"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { makeQueryClient } from "@/lib/query/client";

export function QueryProvider({ children }: { children: ReactNode }) {
  // useState so the client is created once per mount (not per render) and never
  // shared across requests on the server.
  const [client] = useState(makeQueryClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
```

- [ ] **Step 4: Wrap the app in QueryProvider**

In `frontend/src/app/layout.tsx`, add the import and wrap the existing provider tree so `QueryProvider` is outermost (above `AuthProvider`):
```tsx
import { QueryProvider } from "@/lib/query/query-provider";
```
Wrap the existing `<AuthProvider>...</AuthProvider>` subtree:
```tsx
<QueryProvider>
  <AuthProvider>
    <VideoTasksProvider>{children}</VideoTasksProvider>
  </AuthProvider>
</QueryProvider>
```

- [ ] **Step 5: Verify build + existing tests**

Run: `npm run build && npm test`
Expected: build succeeds; all existing tests still pass (9 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json pnpm-lock.yaml frontend/src/lib/query frontend/src/app/layout.tsx
git commit -m "feat(fe): add react-query + radix deps and QueryProvider (#FE-SKELETON-0001)"
```

---

## Task 2: Data-layer hooks (lib/api)

**Files:**
- Create: `frontend/src/lib/api/keys.ts`, `frontend/src/lib/api/hooks.ts`
- Test: `frontend/src/lib/api/hooks.test.tsx`

- [ ] **Step 1: Create query keys**

Create `frontend/src/lib/api/keys.ts`:
```ts
export const videoKeys = {
  all: ["videos"] as const,
  list: () => [...videoKeys.all, "list"] as const,
  detail: (id: string) => [...videoKeys.all, "detail", id] as const
};

export const meKey = ["me"] as const;
export const quotaKey = ["quota"] as const;
```

- [ ] **Step 2: Write the failing test**

Create `frontend/src/lib/api/hooks.test.tsx`:
```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";
import type { ReactNode } from "react";

vi.mock("@/lib/api/videos", () => ({
  listVideos: vi.fn(),
  getVideo: vi.fn(),
  createVideo: vi.fn()
}));
vi.mock("@/lib/api/uploads", () => ({ uploadImage: vi.fn() }));
vi.mock("@/lib/api/auth", () => ({ fetchMe: vi.fn() }));

let mockSession: { token: string } | null = { token: "t" };
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: mockSession, ready: true, login: vi.fn(), logout: vi.fn() })
}));

import { listVideos, createVideo } from "@/lib/api/videos";
import { useVideos, useCreateVideo } from "@/lib/api/hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => {
  vi.clearAllMocks();
  mockSession = { token: "t" };
});

describe("useVideos", () => {
  it("fetches the list when a session is present", async () => {
    (listVideos as Mock).mockResolvedValue([{ id: "v1" }]);
    const { result } = renderHook(() => useVideos(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([{ id: "v1" }]);
  });

  it("does not fetch when there is no session", async () => {
    mockSession = null;
    (listVideos as Mock).mockResolvedValue([]);
    renderHook(() => useVideos(), { wrapper });
    await new Promise((r) => setTimeout(r, 0));
    expect(listVideos).not.toHaveBeenCalled();
  });
});

describe("useCreateVideo", () => {
  it("creates a video via the mutation", async () => {
    (createVideo as Mock).mockResolvedValue({ task_id: "x", status: "queued" });
    const { result } = renderHook(() => useCreateVideo(), { wrapper });
    result.current.mutate({ topic: "hi" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(createVideo).toHaveBeenCalledWith({ topic: "hi" });
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `npx vitest run src/lib/api/hooks.test.tsx`
Expected: FAIL — `useVideos`/`useCreateVideo` not exported from `@/lib/api/hooks` (module missing).

- [ ] **Step 4: Implement the hooks**

Create `frontend/src/lib/api/hooks.ts`:
```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchMe } from "@/lib/api/auth";
import { meKey, quotaKey, videoKeys } from "@/lib/api/keys";
import { uploadImage } from "@/lib/api/uploads";
import { createVideo, getVideo, listVideos } from "@/lib/api/videos";
import type { CreateVideoRequest } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-context";

/** Provisional quota shape — no backend endpoint yet. */
export interface Quota {
  used: number;
  limit: number;
  resetAt: string;
}

export function useVideos() {
  const { session } = useAuth();
  return useQuery({ queryKey: videoKeys.list(), queryFn: listVideos, enabled: !!session });
}

export function useVideo(id: string | undefined) {
  const { session } = useAuth();
  return useQuery({
    queryKey: videoKeys.detail(id ?? ""),
    queryFn: () => getVideo(id as string),
    enabled: !!session && !!id
  });
}

export function useCreateVideo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateVideoRequest) => createVideo(input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: videoKeys.list() });
    }
  });
}

export function useUploadImage() {
  return useMutation({ mutationFn: (file: File) => uploadImage(file) });
}

export function useMe() {
  const { session } = useAuth();
  return useQuery({ queryKey: meKey, queryFn: fetchMe, enabled: !!session });
}

// TODO(#backend quota endpoint): wire queryFn to GET /api/v1/quota when it ships.
export function useQuota() {
  const { session } = useAuth();
  return useQuery<Quota | null>({
    queryKey: quotaKey,
    queryFn: async () => null,
    enabled: !!session
  });
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npx vitest run src/lib/api/hooks.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api/keys.ts frontend/src/lib/api/hooks.ts frontend/src/lib/api/hooks.test.tsx
git commit -m "feat(fe): react-query data-layer hooks (#FE-SKELETON-0001)"
```

---

## Task 3: Extract SSE progress into lib/sse

**Files:**
- Create: `frontend/src/lib/sse/progress-mapping.ts`, `frontend/src/lib/sse/use-task-progress.ts`
- Test: `frontend/src/lib/sse/progress-mapping.test.ts`, `frontend/src/lib/sse/use-task-progress.test.tsx`
- Modify: `frontend/src/lib/videos/tasks-context.tsx`

- [ ] **Step 1: Write the failing mapping test**

Create `frontend/src/lib/sse/progress-mapping.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import { fromVideoRead, labelFor, mapSseStatus, TERMINAL } from "./progress-mapping";

describe("progress-mapping", () => {
  it("maps SSE statuses to UI statuses", () => {
    expect(mapSseStatus("SUCCESS")).toBe("done");
    expect(mapSseStatus("FAILURE")).toBe("failed");
    expect(mapSseStatus("PROGRESS")).toBe("running");
    expect(mapSseStatus(undefined)).toBe("queued");
  });

  it("labels by status", () => {
    expect(labelFor("done", 100)).toBe("已完成");
    expect(labelFor("running", 42)).toBe("生成中 42%");
  });

  it("knows terminal states", () => {
    expect(TERMINAL.includes("done")).toBe(true);
    expect(TERMINAL.includes("running")).toBe(false);
  });

  it("maps a VideoRead to a TrackedTask", () => {
    const t = fromVideoRead({
      id: "v1", title: "T", prompt: "", mode: "x", status: "done",
      progress: 100, created_at: "", playback_url: "u"
    });
    expect(t.taskId).toBe("v1");
    expect(t.status).toBe("done");
    expect(t.playbackUrl).toBe("u");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run src/lib/sse/progress-mapping.test.ts`
Expected: FAIL — `./progress-mapping` does not exist.

- [ ] **Step 3: Create the mapping module (lifted verbatim from tasks-context)**

Create `frontend/src/lib/sse/progress-mapping.ts`:
```ts
import type { VideoRead, VideoStatus } from "@/lib/api/types";

export type UiStatus = VideoStatus; // queued | running | done | failed

export interface TrackedTask {
  taskId: string;
  topic: string;
  status: UiStatus;
  progress: number; // 0..100
  statusLabel: string;
  playbackUrl?: string | null;
  downloadUrl?: string | null;
  thumbnailUrl?: string | null;
  durationSec?: number | null;
  error?: string | null;
}

export const TERMINAL: UiStatus[] = ["done", "failed"];

export function labelFor(status: UiStatus, pct: number): string {
  switch (status) {
    case "done":
      return "已完成";
    case "failed":
      return "失败";
    case "running":
      return `生成中 ${pct}%`;
    default:
      return "排队中";
  }
}

/** SSE frame -> UI status (the stream still uses uppercase worker statuses). */
export function mapSseStatus(status: string | undefined): UiStatus {
  switch ((status ?? "").toUpperCase()) {
    case "SUCCESS":
    case "DONE":
      return "done";
    case "FAILURE":
    case "FAILED":
      return "failed";
    case "PROGRESS":
    case "STARTED":
    case "RUNNING":
      return "running";
    default:
      return "queued";
  }
}

export function fromVideoRead(read: VideoRead): TrackedTask {
  const pct = read.progress ?? 0;
  return {
    taskId: read.id,
    topic: read.title || read.prompt || "未命名视频",
    status: read.status,
    progress: pct,
    statusLabel: labelFor(read.status, pct),
    playbackUrl: read.playback_url ?? null,
    downloadUrl: read.download_url ?? null,
    thumbnailUrl: read.thumbnail_url ?? null,
    durationSec: read.duration_sec ?? null,
    error: read.error ?? null
  };
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npx vitest run src/lib/sse/progress-mapping.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Refactor tasks-context to import the shared helpers (no behavior change)**

In `frontend/src/lib/videos/tasks-context.tsx`:
1. Remove the local definitions of `UiStatus`, `TrackedTask`, `_TERMINAL`, `labelFor`, `mapSseStatus`, `fromVideoRead`.
2. Add the import:
```tsx
import {
  fromVideoRead,
  labelFor,
  mapSseStatus,
  TERMINAL,
  type TrackedTask,
  type UiStatus
} from "@/lib/sse/progress-mapping";
```
3. Replace remaining `_TERMINAL` references with `TERMINAL`.
4. Re-export the types so existing importers keep working:
```tsx
export type { TrackedTask, UiStatus } from "@/lib/sse/progress-mapping";
```

- [ ] **Step 6: Verify the full suite + build still pass**

Run: `npm test && npm run build`
Expected: PASS — all tests (existing + new mapping tests) green; build succeeds.

- [ ] **Step 7: Write the failing useTaskProgress test**

Create `frontend/src/lib/sse/use-task-progress.test.tsx`:
```tsx
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";

vi.mock("@/lib/api/videos", () => ({
  streamVideoEvents: vi.fn(),
  getVideo: vi.fn()
}));

import { getVideo, streamVideoEvents } from "@/lib/api/videos";
import { useTaskProgress } from "./use-task-progress";

afterEach(() => vi.clearAllMocks());

describe("useTaskProgress", () => {
  it("reflects SSE progress events", async () => {
    (streamVideoEvents as Mock).mockImplementation(
      async (_id: string, onMessage: (e: unknown) => void) => {
        onMessage({ status: "PROGRESS", progress: 0.5 });
      }
    );
    (getVideo as Mock).mockResolvedValue({
      id: "v1", title: "T", prompt: "", mode: "x", status: "running",
      progress: 50, created_at: ""
    });
    const { result } = renderHook(() => useTaskProgress("v1"));
    await waitFor(() => expect(result.current.status).toBe("running"));
    expect(result.current.progress).toBe(50);
  });
});
```

- [ ] **Step 8: Run it to verify it fails**

Run: `npx vitest run src/lib/sse/use-task-progress.test.tsx`
Expected: FAIL — `./use-task-progress` does not exist.

- [ ] **Step 9: Implement useTaskProgress**

Create `frontend/src/lib/sse/use-task-progress.ts`:
```ts
"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { getVideo, streamVideoEvents } from "@/lib/api/videos";
import { labelFor, mapSseStatus, TERMINAL, type UiStatus } from "@/lib/sse/progress-mapping";

export interface TaskProgress {
  status: UiStatus;
  progress: number; // 0..100
  statusLabel: string;
  error?: string | null;
}

const INITIAL: TaskProgress = { status: "queued", progress: 0, statusLabel: labelFor("queued", 0) };

/**
 * Single-task progress: subscribe to the SSE stream, fall back to polling if SSE
 * is unavailable, and reconcile the authoritative record on terminal. Returns the
 * live status/percent for one task.
 */
export function useTaskProgress(taskId: string | undefined): TaskProgress {
  const [state, setState] = useState<TaskProgress>(INITIAL);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!taskId) return;
    const controller = new AbortController();
    controllerRef.current = controller;

    const apply = (status: UiStatus, pct: number, error?: string | null) => {
      setState({
        status,
        progress: status === "done" ? 100 : pct,
        statusLabel: labelFor(status, pct),
        error: error ?? undefined
      });
    };

    const poll = async () => {
      while (!controller.signal.aborted) {
        try {
          const read = await getVideo(taskId);
          apply(read.status, read.progress ?? 0, read.error);
          if (TERMINAL.includes(read.status)) return;
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) return;
          return;
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
    };

    streamVideoEvents(
      taskId,
      (event) => {
        if (event.stage === "sse_timeout") return;
        const pct = Math.round((event.progress ?? 0) * 100);
        apply(mapSseStatus(event.status), pct, event.error);
      },
      controller.signal
    )
      .then(async () => {
        if (controller.signal.aborted) return;
        try {
          const read = await getVideo(taskId);
          apply(read.status, read.progress ?? 0, read.error);
        } catch {
          // keep last state
        }
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 401) return;
        void poll();
      });

    return () => controller.abort();
  }, [taskId]);

  return state;
}
```

- [ ] **Step 10: Run it to verify it passes**

Run: `npx vitest run src/lib/sse/use-task-progress.test.tsx`
Expected: PASS (1 test).

- [ ] **Step 11: Commit**

```bash
git add frontend/src/lib/sse frontend/src/lib/videos/tasks-context.tsx
git commit -m "feat(fe): extract SSE reconcile into lib/sse + useTaskProgress (#FE-SKELETON-0001)"
```

---

## Task 4: Relocate console components into feature folders

**Files (git mv + import updates):**
- `components/console/top-bar.tsx` → `components/layout/top-bar.tsx`
- `components/console/sidebar.tsx` → `components/layout/sidebar.tsx`
- `components/console/new-video-card.tsx` → `components/workbench/new-video-card.tsx`
- `components/console/task-list.tsx` → `components/tasks/task-list.tsx`

- [ ] **Step 1: Move the files with git mv**

Run (from `frontend/`):
```bash
mkdir -p src/components/layout src/components/workbench src/components/tasks src/components/video
git mv src/components/console/top-bar.tsx src/components/layout/top-bar.tsx
git mv src/components/console/sidebar.tsx src/components/layout/sidebar.tsx
git mv src/components/console/new-video-card.tsx src/components/workbench/new-video-card.tsx
git mv src/components/console/task-list.tsx src/components/tasks/task-list.tsx
```

- [ ] **Step 2: Update import paths to the moved modules**

Find every importer and update the `@/components/console/...` paths:
```bash
grep -rln "components/console/" src
```
For each hit (notably `src/app/page.tsx`), replace:
- `@/components/console/top-bar` → `@/components/layout/top-bar`
- `@/components/console/sidebar` → `@/components/layout/sidebar`
- `@/components/console/new-video-card` → `@/components/workbench/new-video-card`
- `@/components/console/task-list` → `@/components/tasks/task-list`

Then confirm none remain:
```bash
grep -rn "components/console/" src
```
Expected: no matches; remove the now-empty `src/components/console/` dir if present.

- [ ] **Step 3: Verify build + tests + lint**

Run: `npm run build && npm test && npm run lint`
Expected: build succeeds, tests pass, lint clean.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(fe): relocate console components into feature folders (#FE-SKELETON-0001)"
```

---

## Task 5: Route groups + client auth gate

**Files:**
- Create (git mv): `app/(auth)/login/page.tsx` (from `app/login/page.tsx`)
- Create: `app/(app)/layout.tsx`
- Create (git mv): `app/(app)/page.tsx` (from `app/page.tsx`)

- [ ] **Step 1: Move pages into route groups**

Run (from `frontend/`):
```bash
mkdir -p "src/app/(auth)/login" "src/app/(app)"
git mv src/app/login/page.tsx "src/app/(auth)/login/page.tsx"
git mv src/app/page.tsx "src/app/(app)/page.tsx"
```
(Route groups `(auth)`/`(app)` do not change URLs: `/login` and `/` stay the same.)

- [ ] **Step 2: Create the (app) shell layout with the client auth gate**

Create `frontend/src/app/(app)/layout.tsx`:
```tsx
"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth/auth-context";

// Client-side auth gate: the JWT lives in localStorage, so the gate must run in
// the browser (Next middleware can't read it). True server-side gating arrives
// with #M2-AUTH-COOKIE. Renders an aria-busy placeholder until the session is
// known, so we never flash the console or fire authed requests while logged out.
export default function AppLayout({ children }: { children: ReactNode }) {
  const { session, ready } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (ready && !session) router.replace("/login");
  }, [ready, session, router]);

  if (!ready || !session) return <main className="min-h-screen" aria-busy="true" />;
  return <>{children}</>;
}
```

- [ ] **Step 3: Slim the workbench page (gate now lives in the layout)**

In `frontend/src/app/(app)/page.tsx`, remove the now-duplicated gate (the `useAuth`/`useRouter`/`useEffect` redirect and the `if (!ready || !session) return ...` line), keeping the page's own content (TopBar/Sidebar/NewVideoCard/TaskList and the back-nav). The `onBack` handler and JSX stay. Resulting top of the file:
```tsx
"use client";

import { ChevronLeft, Store } from "lucide-react";
import { useRouter } from "next/navigation";

import { NewVideoCard } from "@/components/workbench/new-video-card";
import { Sidebar } from "@/components/layout/sidebar";
import { TaskList } from "@/components/tasks/task-list";
import { TopBar } from "@/components/layout/top-bar";

export default function Home() {
  const router = useRouter();

  const onBack = () => {
    if (typeof window !== "undefined" && window.history.length > 1) router.back();
    else router.push("/");
  };

  return (
    // ...existing JSX unchanged...
  );
}
```

- [ ] **Step 4: Verify build + tests + lint, and confirm routes still resolve**

Run: `npm run build && npm test && npm run lint`
Expected: build lists routes `/` and `/login` (route groups are transparent); tests pass; lint clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(fe): route groups (auth)/(app) + client auth gate in shell layout (#FE-SKELETON-0001)"
```

---

## Task 6: middleware shell

**Files:**
- Create: `frontend/src/middleware.ts`

- [ ] **Step 1: Create the thin middleware**

Create `frontend/src/middleware.ts`:
```ts
import { NextResponse, type NextRequest } from "next/server";

// Thin shell only. The session JWT lives in localStorage (client-side), which
// middleware cannot read, so real auth gating happens in app/(app)/layout.tsx.
// A true server-side gate arrives with #M2-AUTH-COOKIE (httpOnly cookie); at that
// point this is where we redirect unauthenticated requests away from (app) routes.
export function middleware(_request: NextRequest) {
  return NextResponse.next();
}

export const config = {
  // Run on app routes, excluding Next internals and static assets.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"]
};
```

- [ ] **Step 2: Verify build**

Run: `npm run build`
Expected: build succeeds and reports middleware compiled.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/middleware.ts
git commit -m "feat(fe): add middleware shell (true gate deferred to #M2-AUTH-COOKIE) (#FE-SKELETON-0001)"
```

---

## Task 7: Radix primitive wrappers + QuotaBadge

**Files:**
- Create: `frontend/src/components/ui/tooltip.tsx` (representative wrapper)
- Create: `frontend/src/components/ui/dialog.tsx`, `select.tsx`, `popover.tsx`, `tabs.tsx`
- Create: `frontend/src/components/layout/quota-badge.tsx`

- [ ] **Step 1: Create the Tooltip wrapper (behavior from Radix, glass skin via tokens)**

Create `frontend/src/components/ui/tooltip.tsx`:
```tsx
"use client";

import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { cn } from "@/lib/utils";

export const TooltipProvider = TooltipPrimitive.Provider;

export function Tooltip({ content, children }: { content: ReactNode; children: ReactNode }) {
  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          sideOffset={6}
          className={cn(
            "z-50 rounded-field border border-line-gold bg-glass-soft px-2.5 py-1.5",
            "text-[12.5px] text-ink-soft shadow-focus-gold"
          )}
        >
          {content}
          <TooltipPrimitive.Arrow className="fill-line-gold" />
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

export type TooltipContentProps = ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>;
```

- [ ] **Step 2: Create the remaining primitive wrappers**

Create `frontend/src/components/ui/dialog.tsx`:
```tsx
"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;

export function DialogContent({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm" />
      <DialogPrimitive.Content
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-[min(92vw,520px)] -translate-x-1/2 -translate-y-1/2",
          "rounded-card border border-line-gold bg-glass p-6 shadow-focus-gold",
          className
        )}
      >
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}
```

Create `frontend/src/components/ui/popover.tsx`:
```tsx
"use client";

import * as PopoverPrimitive from "@radix-ui/react-popover";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;

export function PopoverContent({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        sideOffset={6}
        className={cn(
          "z-50 rounded-field border border-line-gold bg-glass p-3 shadow-focus-gold",
          className
        )}
      >
        {children}
      </PopoverPrimitive.Content>
    </PopoverPrimitive.Portal>
  );
}
```

Create `frontend/src/components/ui/tabs.tsx`:
```tsx
"use client";

import * as TabsPrimitive from "@radix-ui/react-tabs";

export const Tabs = TabsPrimitive.Root;
export const TabsList = TabsPrimitive.List;
export const TabsTrigger = TabsPrimitive.Trigger;
export const TabsContent = TabsPrimitive.Content;
```

Create `frontend/src/components/ui/select.tsx`:
```tsx
"use client";

import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export const Select = SelectPrimitive.Root;
export const SelectValue = SelectPrimitive.Value;

export function SelectTrigger({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <SelectPrimitive.Trigger
      className={cn(
        "inline-flex items-center justify-between gap-2 rounded-field border border-line-gold",
        "bg-glass-soft px-3 py-2 text-[13px] text-ink outline-none focus-visible:shadow-focus-gold",
        className
      )}
    >
      {children}
      <SelectPrimitive.Icon>
        <ChevronDown size={15} />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

export function SelectContent({ children }: { children: ReactNode }) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content className="z-50 overflow-hidden rounded-field border border-line-gold bg-glass">
        <SelectPrimitive.Viewport className="p-1">{children}</SelectPrimitive.Viewport>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

export function SelectItem({ value, children }: { value: string; children: ReactNode }) {
  return (
    <SelectPrimitive.Item
      value={value}
      className="flex cursor-pointer items-center gap-2 rounded-[8px] px-2.5 py-1.5 text-[13px] text-ink-soft outline-none data-[highlighted]:bg-glass-soft data-[highlighted]:text-ink"
    >
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
      <SelectPrimitive.ItemIndicator>
        <Check size={14} />
      </SelectPrimitive.ItemIndicator>
    </SelectPrimitive.Item>
  );
}
```

- [ ] **Step 3: Create QuotaBadge consuming useQuota**

Create `frontend/src/components/layout/quota-badge.tsx`:
```tsx
"use client";

import { useQuota } from "@/lib/api/hooks";

// Provisional: useQuota returns null until the backend quota endpoint exists.
// Renders nothing rather than a fake number, so we never show misleading data.
export function QuotaBadge() {
  const { data } = useQuota();
  if (!data) return null;
  return (
    <span className="rounded-pill border border-line-gold bg-glass-soft px-3 py-1.5 text-[12px] text-ink-soft">
      额度 {data.used}/{data.limit}
    </span>
  );
}
```

- [ ] **Step 4: Mount QuotaBadge in the top bar**

In `frontend/src/components/layout/top-bar.tsx`, import and render `QuotaBadge` in the existing top-bar action area:
```tsx
import { QuotaBadge } from "@/components/layout/quota-badge";
```
Place `<QuotaBadge />` alongside the existing top-bar controls (e.g., just before the notifications/settings buttons).

- [ ] **Step 5: Verify build + lint + tests**

Run: `npm run build && npm run lint && npm test`
Expected: build succeeds, lint clean, tests pass. (QuotaBadge renders null, so M2 layout is unchanged.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ui frontend/src/components/layout
git commit -m "feat(fe): radix primitive wrappers + provisional QuotaBadge (#FE-SKELETON-0001)"
```

---

## Task 8: Final verification gate

**Files:** none (verification only).

- [ ] **Step 1: Full unit suite**

Run (from `frontend/`): `npm test`
Expected: PASS — existing 9 + new hook/mapping/progress tests, 0 failures.

- [ ] **Step 2: Lint + type-check via build**

Run: `npm run lint && npm run build`
Expected: lint clean; build succeeds with valid types; routes `/` and `/login` present; middleware compiled.

- [ ] **Step 3: Prove UI never fetches directly (铁律)**

Run (from `frontend/`):
```bash
grep -rnE "fetch\(|axios" src/components || echo "OK: no direct fetch/axios in components"
```
Expected: `OK: ...` (the only `fetch`/`axios` usages live in `lib/api` and `lib/sse`).

- [ ] **Step 4: M2 regression via the live stack (browse skill)**

With the Docker stack up (`infra/docker-compose.full.yml`), rebuild the frontend image and run the browse flow: login (`huading` / `qa@huading.test`) → workbench → 新建 → SSE 进度 → 成片播放; refresh → still logged in, history present, `GET /api/v1/videos → 200`, console clean. Capture a screenshot.

- [ ] **Step 5: Commit any incidental fixes**

```bash
git add -A && git commit -m "chore(fe): skeleton verification fixes (#FE-SKELETON-0001)"
```
(Skip if nothing changed.)

---

## Post-plan (task-package gates, handled outside this plan)

- **Code Simplifier** (去重): collapse any UI structure repeated >2 times.
- **Webapp Testing (Playwright)** + **Chrome DevTools**: the live M2 regression in Task 8 Step 4.
- **Code Review (Codex B)**: open PR `feature/fe-skeleton` base=develop (push 用户手动; PR/合并 Claude 经 Chrome).

---

## Self-Review

- **Spec coverage:** feature-based dirs → Task 4; React Query + hooks → Tasks 1-2; UI-no-fetch rule → Task 8 Step 3; useTaskProgress (reconcile) → Task 3; Radix primitives → Task 7; design tokens → reused via `cn` + semantic classes (Task 7, audited in Task 8 build); route groups + auth gate → Task 5; middleware → Task 6; QuotaBadge → Task 7; provisional useQuota → Task 2; keep custom authStore → unchanged (no task touches it); tests/lint/build/grep gates → Task 8.
- **Placeholder scan:** no TBD/unspecified steps; every code step has complete code; `useQuota`'s `queryFn` is a deliberate provisional with a documented TODO, not a gap.
- **Type consistency:** `videoKeys`/`meKey`/`quotaKey` defined in Task 2 and used in hooks; `TrackedTask`/`UiStatus`/`TERMINAL`/`mapSseStatus`/`labelFor`/`fromVideoRead` defined in Task 3 mapping module and imported by both tasks-context and useTaskProgress; `Quota` shape consistent between `useQuota` (Task 2) and `QuotaBadge` (Task 7); hook names (`useVideos`/`useVideo`/`useCreateVideo`/`useUploadImage`/`useMe`/`useQuota`/`useTaskProgress`) match the spec exactly.
