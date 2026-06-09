# syntax=docker/dockerfile:1
# Backend image: FastAPI API + Celery worker + distilled video engine.
# Build context = backend/.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# Optional package mirrors for restricted networks. Empty = upstream defaults
# (deb.debian.org / pypi.org / playwright CDN). Set these for CN / corporate
# networks, e.g. APT_MIRROR=mirrors.tuna.tsinghua.edu.cn.
ARG APT_MIRROR=""
ARG UV_INDEX=""
ARG PLAYWRIGHT_DOWNLOAD_HOST=""

# System deps: ffmpeg (engine concat/segments), CJK fonts (frame rendering),
# curl/ca-certs (healthcheck + downloads). Playwright's browser system libs are
# installed below via `playwright install --with-deps`.
RUN if [ -n "$APT_MIRROR" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
        [ -f "$f" ] && sed -i "s|deb.debian.org|$APT_MIRROR|g; s|security.debian.org|$APT_MIRROR|g" "$f"; \
      done; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg curl ca-certificates fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# uv (pinned) for reproducible, fast dependency install.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml uv.lock ./
RUN if [ -n "$UV_INDEX" ]; then export UV_DEFAULT_INDEX="$UV_INDEX"; fi; \
    uv sync --frozen --no-dev

# App source (engine package + runtime resources included).
COPY . .

# Playwright Chromium + its OS libraries (HTML frame rendering). Uses the
# bundled browser, so ENGINE_BROWSER_CHANNEL stays empty.
RUN if [ -n "$PLAYWRIGHT_DOWNLOAD_HOST" ]; then export PLAYWRIGHT_DOWNLOAD_HOST="$PLAYWRIGHT_DOWNLOAD_HOST"; fi; \
    playwright install --with-deps chromium

EXPOSE 8000

# Default to the API; the worker service overrides this command in compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
