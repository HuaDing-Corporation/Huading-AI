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

## Checks

```powershell
uv run ruff check .
uv run pytest
```
