# #M2-AUTH-FIX — refresh-logout + list hydrate 401

## Context

e2e testing found two P1s that together break B4 ("refresh keeps history"):

1. **Refresh = logout** — after refresh the user is bounced to `/login`.
2. **List hydrate 401** — `GET /api/v1/videos` returns 401 on console mount (no
   auth), so the history list is always empty — even though `auth/me` and
   `auth/login` work in the same session.

## Root cause (systematic-debugging, confirmed)

Provider tree (layout): `<AuthProvider>` → `<VideoTasksProvider>` → page.

React flushes **child effects before parent effects** on mount, so:

1. `VideoTasksProvider`'s mount hydrate (`listVideos()`) runs **first**.
   `authHeaders()` reads `authStore.get()`, which is still `null` because
   `AuthProvider` hydrates `authStore` only inside *its* effect (which hasn't run
   yet). → `GET /api/v1/videos` is sent with **no `Authorization`** → backend 401.
2. `apiFetch`'s 401 handler calls `authStore.clear()` → **deletes
   `huading.session` from localStorage**.
3. `AuthProvider`'s effect then hydrates → session is gone → `ready && !session`
   → page redirects to `/login`.

So **both P1s share one root cause**: a tokenless videos request fires before the
session is loaded, and its 401 destroys the otherwise-valid persisted session.
The "token only in memory / localStorage empty" hypothesis is a misdiagnosis —
the token *is* persisted (`store.ts`); it's wiped by this spurious 401-clear.

## Approach — 快 (this round)

Defense-in-depth; each layer fixes the root cause from a different angle:

- **Fix 1 — gate the hydrate.** `VideoTasksProvider` reads `useAuth()`; it only
  runs `listVideos()`/subscriptions when a `session` exists, and clears its list
  + aborts streams on logout. No tokenless request; list loads authenticated.
- **Fix 2 — conditional 401-clear.** `apiFetch` only calls `handleUnauthorized()`
  when the request actually carried an `Authorization` header. A tokenless 401
  can never log the user out; a genuinely-expired authed token still clears +
  redirects (correct).
- **Fix 3 — store self-hydration.** `authStore.get()` lazily reads localStorage
  on first client call, so `authHeaders()` is reliable regardless of React effect
  order (removes the race at the source). `set()`/`clear()` mark hydrated so a
  later `get()` never re-reads stale storage.

## 稳 → M3 (recorded, not this round)

Backend issues an httpOnly, SameSite cookie on login; frontend uses
`credentials: "include"`; CORS allows credentials. Removes the JS-reachable token
(XSS hardening). Tracked as **`#M2-AUTH-COOKIE`**.

## Files

- `frontend/src/lib/auth/store.ts` — lazy/sync hydration (Fix 3).
- `frontend/src/lib/api/client.ts` — conditional 401-clear (Fix 2).
- `frontend/src/lib/videos/tasks-context.tsx` — gate hydrate on session (Fix 1).

No backend change.

## Verification

- **TDD unit tests** — bootstrap Vitest + jsdom + Testing Library (the M2
  frontend test runner; the repo currently has only a `test` placeholder):
  - `apiFetch`: a tokenless 401 does **not** clear a session; an authed 401 does.
  - `authStore`: `get()` returns the persisted session without an explicit
    `hydrate()` call (simulates pre-AuthProvider read).
  - tasks hydrate: no fetch when no session; fetches once when session appears.
- **Webapp Testing (Playwright):** login → generate → **refresh** → still in
  console, history present, player plays.
- **Chrome DevTools:** `GET /api/v1/videos` = 200, no 401, console clean.
