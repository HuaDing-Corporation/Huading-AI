# Backend

FastAPI service with configuration management, request IDs, CORS, unified response envelopes, exception handlers, OpenAPI docs, Celery/Redis task wiring, SQLAlchemy/PostgreSQL access, and OSS/S3-compatible object storage with a local fallback.

## Development

```powershell
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open docs at `http://localhost:8000/docs`.

## Local Services

```powershell
docker compose -f ../infra/docker-compose.yml up -d redis postgres
```

## Worker

```powershell
uv run celery -A app.workers.celery_app.celery_app worker --loglevel=info --pool=solo -Q default
```

## Demo Task

Submit:

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/tasks/demo -H "Content-Type: application/json" -d "{\"message\":\"hello\"}"
```

Check status:

```powershell
curl.exe http://127.0.0.1:8000/api/v1/tasks/<task_id>
```

## Video generation (engine task layer)

Async "topic → finished video" via FastAPI + Celery + the distilled engine
(`app/engine`). Keys are injected from the environment (`ENGINE_LLM_*`,
`ENGINE_DASHSCOPE_API_KEY`), never hardcoded — see `.env.example`.

Submit a job (returns a `task_id`):

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/videos -H "Content-Type: application/json" -d "{\"topic\":\"如何提高学习效率\",\"n_scenes\":3}"
```

Poll status / progress (`status`, `progress` 0..1, `stage`, `video_url`):

```powershell
curl.exe http://127.0.0.1:8000/api/v1/videos/<task_id>
```

Or subscribe to live progress over SSE:

```powershell
curl.exe -N http://127.0.0.1:8000/api/v1/videos/<task_id>/events
```

The finished `final.mp4` is uploaded through the object-storage layer at the
task-isolated key `videos/<task_id>/final.mp4`; `video_url` is the returned
location (a `file://` URI under local storage, `s3://…` under S3/OSS).

### Request parameters

`topic` (required), `pipeline` (`standard`|`custom`, default `standard`),
`mode` (`generate`|`fixed`), `n_scenes`, `frame_template`
(e.g. `1080x1920/static_default.html`), `voice`, `tts_speed`.
There is **no** `output_path` parameter: the output path is chosen server-side
under a whitelisted, task-isolated location, so a client cannot write arbitrary
paths (#002-RV P2). `frame_template` is validated against path traversal.

### Concurrency constraint (single-config / single-process)

The engine configuration is a **process-wide singleton** (`config_manager` +
`PIXELLE_VIDEO_ROOT`). Until per-task instantiation lands in M2, run the video
worker single-process so concurrent tasks can't race on shared config:

```powershell
uv run celery -A app.workers.celery_app.celery_app worker --loglevel=info --pool=solo --concurrency=1 -Q default
```

Single-tenant only for now; multi-tenant key resolution is M2.

### Distributed end-to-end (real Redis + worker)

On a machine with Docker, one command brings up Redis (broker+backend), an
independent Celery worker (`-c 1`, solo pool) and the API, then drives a real
`POST → task_id → SSE/poll progress → final video URL` and asserts success:

```bash
ENGINE_LLM_API_KEY=sk-... \
ENGINE_LLM_BASE_URL=https://api.deepseek.com \
ENGINE_LLM_MODEL=deepseek-v4-flash \
ENGINE_BROWSER_CHANNEL=chrome \
  bash backend/scripts/e2e_distributed.sh
```

What it does:
- `infra/docker-compose.yml` → starts the `redis` service (ports 6379).
- Launches `uvicorn app.main:app` and `celery -A app.workers.celery_app.celery_app
  worker --pool=solo -c 1` as separate host processes (true distributed path,
  not eager).
- `scripts/e2e_distributed_driver.py` submits a job over HTTP, prints SSE +
  polled progress, and verifies the returned `video_url` (and the on-disk
  artifact for local storage). Exit code 0 = pass.
- Tears down processes and stops Redis on exit (`--keep` leaves them running).

Notes:
- `ENGINE_BROWSER_CHANNEL=chrome` uses a system-installed Chrome. On a headless
  Linux box without it, drop that var and run `uv run playwright install chromium`
  first.
- Requires `docker` (compose v2) and `uv` on PATH.

## Checks

```powershell
uv run ruff check .
uv run pytest
```
