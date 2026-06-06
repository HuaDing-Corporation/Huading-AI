# Development

## Backend

```powershell
cd backend
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Worker

```powershell
cd backend
uv run celery -A app.workers.celery_app.celery_app worker --loglevel=info
```

## Frontend

```powershell
cd frontend
pnpm install
pnpm dev
```

## Local Services

```powershell
docker compose -f infra/docker-compose.yml up -d
```
