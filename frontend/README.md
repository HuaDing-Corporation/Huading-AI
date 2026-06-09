# Frontend

华鼎 控制台 — Next.js + TypeScript + Tailwind, liquid-glass design system, wired to
the backend (auth + tenant + video generation).

```powershell
pnpm install
pnpm dev   # http://localhost:3000
```

## Configuration

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000   # FastAPI backend base URL
```

See `.env.example`. Copy to `.env.local` and adjust per environment.

## Architecture

- `src/lib/api/client.ts` — single fetch wrapper: injects `Authorization: Bearer`
  + `X-Tenant-ID`, unwraps the `{data,error,request_id}` envelope, throws a typed
  `ApiError`, and clears the session on `401`.
- `src/lib/api/{auth,videos}.ts` — typed endpoint calls (login, me, createVideo,
  getVideoStatus, streamVideoEvents). SSE is consumed over `fetch` (EventSource
  can't send auth headers) with a polling fallback.
- `src/lib/auth/` — `store.ts` (token persistence in localStorage) + `auth-context.tsx`
  (`AuthProvider` / `useAuth`).
- `src/lib/videos/tasks-context.tsx` — tracks videos submitted this session and
  subscribes to live progress.
- Route guards: `/` redirects to `/login` when unauthenticated; `/login` redirects
  to `/` when already signed in.

Token storage note: the JWT lives in `localStorage` (the backend returns it in the
body). Moving to an httpOnly+SameSite cookie set by the backend is the M3 hardening.

## End-to-end verification (requires Docker)

On a host with Docker:

```bash
# 1. Infra
docker compose -f ../infra/docker-compose.yml up -d redis postgres

# 2. Backend (set JWT_SECRET_KEY 32+ chars and ENGINE_LLM_* for real generation)
cd ../backend
uv run alembic upgrade head
JWT_SECRET_KEY=<32+ chars> uv run uvicorn app.main:app --port 8000 &
# single-config / single-process worker:
JWT_SECRET_KEY=<...> ENGINE_LLM_API_KEY=<...> ENGINE_LLM_BASE_URL=<...> ENGINE_LLM_MODEL=<...> \
  ENGINE_BROWSER_CHANNEL=chrome \
  uv run celery -A app.workers.celery_app.celery_app worker --pool=solo -c 1 -Q default &

# 3. Seed a tenant + admin
curl -X POST http://localhost:8000/api/v1/auth/register-tenant \
  -H 'Content-Type: application/json' \
  -d '{"tenant_slug":"huading","tenant_name":"华鼎","email":"admin@huading.ai","password":"changeme123"}'

# 4. Frontend
cd ../frontend && pnpm dev
```

Then open http://localhost:3000 → login (`huading` / `admin@huading.ai` / `changeme123`)
→ 新建视频 → watch real SSE progress → 成片 URL.
