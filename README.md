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

Backend API docs are available at `http://localhost:8000/docs`.

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

## Secret scanning

CI runs gitleaks (`.github/workflows/secret-scan.yml`) over the **full git
history** using `.gitleaks.toml`.

- **After changing any secret-scan-related file** (`.gitleaks.toml`, the
  workflow, or any file that adds/edits a token-shaped string — including test
  fixtures), re-run a **full-history** scan, not just a working-tree check:

  ```bash
  # Either scan history in place …
  gitleaks detect --source . --config .gitleaks.toml --no-banner
  # … or, to mirror CI exactly (fresh checkout, no local orphan commits/reflog):
  git clone --branch <your-branch> . /tmp/scan && \
    gitleaks detect --source /tmp/scan --config /tmp/scan/.gitleaks.toml --no-banner
  ```

  Rationale: `gitleaks detect` scans every commit. A token added in one commit is
  caught even if a later commit removes it, so a working-tree-only check (or a
  scan run before the file existed) can pass locally while CI fails.

- Never commit real secrets or secret-shaped literals. Build test fixtures from
  parts (e.g. `"sk-" + "x" * 20`) so they don't trip the scanner. If a real
  secret is committed, treat it as a leak: rotate it and remove it from history
  (don't just allowlist it).

## License

This repository is licensed under Apache-2.0. It is distilled from Pixelle-Video, also Apache-2.0 licensed; attribution is retained in `NOTICE`.
