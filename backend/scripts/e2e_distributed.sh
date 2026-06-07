#!/usr/bin/env bash
#
# One-command distributed end-to-end for the video task layer.
#
#   ENGINE_LLM_API_KEY=... ENGINE_LLM_BASE_URL=... ENGINE_LLM_MODEL=... \
#     bash backend/scripts/e2e_distributed.sh
#
# Brings up Redis (Docker), an independent Celery worker (-c 1, solo pool), and
# the FastAPI app, then drives a real POST -> SSE/poll progress -> final video
# URL through HTTP and asserts success. Tears everything down on exit.
#
# Flags:
#   --keep   leave Redis + processes running after the run (for debugging)
#
# Requires: docker (compose v2), uv, and a system browser if
# ENGINE_BROWSER_CHANNEL=chrome (otherwise run `uv run playwright install chromium`).
set -euo pipefail

KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$BACKEND_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_DIR/infra/docker-compose.yml"
LOG_DIR="$(mktemp -d)"
API_LOG="$LOG_DIR/api.log"
WORKER_LOG="$LOG_DIR/worker.log"

API_PID=""
WORKER_PID=""

log() { echo "[e2e] $*"; }

require_env() {
  local missing=0
  for v in ENGINE_LLM_API_KEY ENGINE_LLM_BASE_URL ENGINE_LLM_MODEL; do
    if [ -z "${!v:-}" ]; then echo "ERROR: $v is not set" >&2; missing=1; fi
  done
  [ "$missing" = "0" ] || exit 2
}

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" "$@"
  else
    docker-compose -f "$COMPOSE_FILE" "$@"
  fi
}

cleanup() {
  local code=$?
  if [ "$KEEP" = "1" ]; then
    log "--keep set; leaving services running. Logs: $LOG_DIR"
    return
  fi
  log "tearing down..."
  [ -n "$WORKER_PID" ] && kill "$WORKER_PID" 2>/dev/null || true
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  compose stop redis >/dev/null 2>&1 || true
  if [ "$code" != "0" ]; then
    echo "----- api.log (tail) -----"; tail -n 30 "$API_LOG" 2>/dev/null || true
    echo "----- worker.log (tail) -----"; tail -n 40 "$WORKER_LOG" 2>/dev/null || true
  fi
  exit "$code"
}
trap cleanup EXIT

require_env
command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found" >&2; exit 2; }

cd "$BACKEND_DIR"

log "starting Redis (Docker)..."
compose up -d --wait redis 2>/dev/null || compose up -d redis
# Wait for redis to answer PING (covers compose without --wait support).
for _ in $(seq 1 30); do
  if compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then break; fi
  sleep 1
done

log "syncing deps..."
uv sync >/dev/null 2>&1 || log "uv sync skipped/failed (continuing with existing env)"

export CELERY_TASK_ALWAYS_EAGER=false

log "starting API (uvicorn) ..."
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 >"$API_LOG" 2>&1 &
API_PID=$!
for _ in $(seq 1 40); do
  if curl -sf http://127.0.0.1:8000/openapi.json >/dev/null 2>&1; then break; fi
  sleep 0.5
done

log "starting Celery worker (-c 1, solo pool) ..."
uv run celery -A app.workers.celery_app.celery_app worker \
  --pool=solo --concurrency=1 -Q default --loglevel=info >"$WORKER_LOG" 2>&1 &
WORKER_PID=$!
sleep 4  # let the worker register before we submit

log "running HTTP driver ..."
uv run python scripts/e2e_distributed_driver.py
log "PASS — distributed e2e succeeded"
