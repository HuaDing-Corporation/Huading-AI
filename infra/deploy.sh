#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DRY_RUN="${DRY_RUN:-0}"
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
  shift
fi

COMPOSE_FILE="${COMPOSE_FILE:-infra/docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-infra/.env}"
dc=(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE")

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  "$@"
}

run git pull --ff-only
run "${dc[@]}" up -d --build
run "${dc[@]}" restart nginx
run "${dc[@]}" exec -T backend alembic current
run "${dc[@]}" ps
