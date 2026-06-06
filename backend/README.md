# Backend

FastAPI service with Celery, Redis, SQLAlchemy, and PostgreSQL wiring.

## Development

```powershell
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Checks

```powershell
uv run ruff check .
uv run pytest
```
