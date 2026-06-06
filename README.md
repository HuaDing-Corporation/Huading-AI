# 华鼎 AI Monorepo

华鼎项目采用前后端分离的 monorepo 结构，后端使用 Python 3.11、FastAPI、Celery、Redis、PostgreSQL，前端使用 Next.js、TypeScript、Tailwind CSS 与 shadcn/ui 风格组件。

## Repository Layout

```text
.
├── backend/       # FastAPI API service, Celery worker, database access
├── frontend/      # Next.js application
├── docs/          # Engineering and collaboration docs
├── infra/         # Local and deployment infrastructure
├── .github/       # Pull request, issue templates, CI
├── LICENSE        # Apache-2.0
└── NOTICE         # Pixelle-Video attribution notice
```

Existing planning document folders such as `01-*`, `02-*`, and `03-*` are intentionally preserved when present.

## Prerequisites

- Python 3.11
- uv 0.5+ or another PEP 621 compatible Python workflow
- Node.js 20+
- pnpm 9+
- Docker Compose for local PostgreSQL and Redis

## Quick Start

```powershell
pnpm install
pnpm dev
```

Run backend only:

```powershell
cd backend
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Run frontend only:

```powershell
cd frontend
pnpm install
pnpm dev
```

Optional local services:

```powershell
docker compose -f infra/docker-compose.yml up -d
```

## Branching

- `main`: protected release branch
- `develop`: integration branch
- `feature/*`: feature branches
- `fix/*`: bug-fix branches

All changes should go through pull requests with linked issues when possible.

## License

This repository is licensed under Apache-2.0. It is distilled from Pixelle-Video, also Apache-2.0 licensed; attribution is retained in `NOTICE`.
