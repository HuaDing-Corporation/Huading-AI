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

## Seedance video (text-to-video / image-to-video)

Doubao-Seedance 2.0 via Volcengine Ark. Keys come from env (never hardcoded);
the engine entry point is `app.engine.generate_seedance_video(cfg, ...)` for
Phase-2 pipelines. Verify with your own Ark key:

```powershell
$env:SEEDANCE_API_KEY="..."                                   # required
$env:SEEDANCE_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"  # optional
$env:SEEDANCE_MODEL="doubao-seedance-2-0-260128"             # optional

# Text-to-video
uv run python scripts/run_seedance.py --mode t2v --prompt "一只柯基在草地奔跑，电影质感"

# Image-to-video (local file or http(s) URL; omit --image for a generated sample)
uv run python scripts/run_seedance.py --mode i2v --image .\product.jpg --prompt "镜头缓慢推近，柔光"
```

Each prints the task id, remote video URL, and the saved mp4 path.

### Seedance pipelines via the API (video_mode)

`POST /api/v1/videos` accepts `video_mode`:

- `static_template` (default) — the existing HTML-frame pipeline (unchanged).
- `seedance_t2v` — topic → LLM scene plan → Seedance text-to-video clips →
  edge-tts voiceover → ffmpeg compose (+BGM).
- `seedance_i2v` — same flow, but every clip is conditioned on an uploaded
  product image: first `POST /api/v1/uploads` (multipart `file`; jpeg/png/webp,
  ≤10MB; stored under `tenants/{tenant_id}/uploads/...`), then pass the returned
  `key` as `image_key`.

Upload bodies are capped by the API before multipart parsing using
`UPLOAD_MAX_BYTES` (default `10485760`, 10 MiB). In production, also set the same
limit at the reverse proxy layer (for example nginx `client_max_body_size 10m`,
or the equivalent Traefik/body-size middleware) so oversized uploads are rejected
before they reach the application process.

Requires `ENGINE_SEEDANCE_API_KEY` (plus the `ENGINE_LLM_*` credentials) on the
worker. Progress/SSE and the tenant-isolated output key work exactly as for
static_template. Example:

```bash
# 1) upload the product shot
curl -X POST http://127.0.0.1:8000/api/v1/uploads \
  -H "Authorization: Bearer $TOKEN" -F "file=@product.jpg"
# -> {"data": {"key": "uploads/<uuid>.jpg", ...}}

# 2) generate (i2v)
curl -X POST http://127.0.0.1:8000/api/v1/videos \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"topic":"秋冬羊绒大衣种草","video_mode":"seedance_i2v","image_key":"uploads/<uuid>.jpg","n_scenes":2}'
```

## Checks

```powershell
uv run ruff check .
uv run pytest
```
