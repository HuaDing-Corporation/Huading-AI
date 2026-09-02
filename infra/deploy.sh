#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DRY_RUN="${DRY_RUN:-0}"
DEPLOY_MODE=""
RELEASE_SHA=""
STOP_GRACE_SECONDS="${STOP_GRACE_SECONDS:-1800}"
DRAIN_TIMEOUT_SECONDS="${DRAIN_TIMEOUT_SECONDS:-1800}"
DRAIN_POLL_SECONDS="${DRAIN_POLL_SECONDS:-10}"
BROKER_GATE_TIMEOUT_SECONDS="${BROKER_GATE_TIMEOUT_SECONDS:-300}"
WORKER_READY_TIMEOUT_SECONDS="${WORKER_READY_TIMEOUT_SECONDS:-180}"
CURRENT_DB_REVISION=""
TARGET_CODE_HEAD=""

usage() {
  cat <<'EOF'
Usage: bash infra/deploy.sh [--dry-run] --routine --release-sha <approved-sha>

  --routine           Routine release after pricing closure is already complete.
  --release-sha SHA   Exact approved commit to fast-forward and deploy.
  --dry-run           Print commands without running them.

First startup and the initial pricing-closure rollout must use the write-free
staged release procedure in infra/DEPLOY.md; this shortcut refuses those states.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --routine)
      if [[ -n "$DEPLOY_MODE" ]]; then
        printf 'Only one deploy mode may be selected.\n' >&2
        usage >&2
        exit 64
      fi
      DEPLOY_MODE="$1"
      ;;
    --release-sha)
      if [[ $# -lt 2 || -z "${2:-}" ]]; then
        printf '%s requires an approved commit SHA.\n' "$1" >&2
        usage >&2
        exit 64
      fi
      RELEASE_SHA="$2"
      shift
      ;;
    --help | -h)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 64
      ;;
  esac
  shift
done

if [[ -z "$DEPLOY_MODE" ]]; then
  printf 'A deploy mode is required; refusing an ambiguous production deploy.\n' >&2
  usage >&2
  exit 64
fi
if [[ -z "$RELEASE_SHA" ]]; then
  printf 'Routine deploy refused: --release-sha is required.\n' >&2
  usage >&2
  exit 64
fi
if [[ ! "$RELEASE_SHA" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
  printf 'Routine deploy refused: release SHA must contain 7 to 40 hexadecimal characters.\n' >&2
  exit 64
fi
for numeric_setting in \
  STOP_GRACE_SECONDS \
  DRAIN_TIMEOUT_SECONDS \
  DRAIN_POLL_SECONDS \
  BROKER_GATE_TIMEOUT_SECONDS \
  WORKER_READY_TIMEOUT_SECONDS; do
  numeric_value="${!numeric_setting}"
  if [[ ! "$numeric_value" =~ ^[1-9][0-9]*$ ]]; then
    printf 'Routine deploy refused: %s must be a positive integer.\n' "$numeric_setting" >&2
    exit 64
  fi
done

COMPOSE_FILE="${COMPOSE_FILE:-infra/docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-infra/.env}"
# Routine releases have already proved that the running database revision and
# target image head are identical. Never let backend startup mutate that state.
BACKEND_AUTO_MIGRATE=0
dc=(env BACKEND_AUTO_MIGRATE="$BACKEND_AUTO_MIGRATE" docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE")

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

fail_closed_after_writer_shutdown() {
  local message="$1"
  printf '%s\n' "$message" >&2
  set +e
  "${dc[@]}" stop nginx
  "${dc[@]}" stop -t "$STOP_GRACE_SECONDS" backend
  "${dc[@]}" stop frontend
  "${dc[@]}" stop -t "$STOP_GRACE_SECONDS" worker worker-image worker-video
  set -e
  exit 2
}

extract_single_head_revision() {
  local output="$1"
  local revisions=()
  mapfile -t revisions < <(
    sed -nE 's/^([[:alnum:]_]+)[[:space:]]+\(head\).*$/\1/p' <<<"$output" | sort -u
  )
  if [[ "${#revisions[@]}" -ne 1 || -z "${revisions[0]}" ]]; then
    return 1
  fi
  printf '%s\n' "${revisions[0]}"
}

routine_release_guard() {
  if [[ "$DRY_RUN" == "1" ]]; then
    run "${dc[@]}" exec -T backend alembic current
    run "${dc[@]}" exec -T backend \
      python scripts/ops/pricing_closure_readiness_cli.py audit
    CURRENT_DB_REVISION="20260829_0038"
    return 0
  fi

  local branch
  branch=$(git branch --show-current)
  if [[ "$branch" != "develop" ]]; then
    printf 'Routine deploy refused: production checkout must be on develop, found %s.\n' "$branch" >&2
    exit 2
  fi
  if [[ -n "$(git status --porcelain)" ]]; then
    printf 'Routine deploy refused: production checkout has uncommitted changes.\n' >&2
    exit 2
  fi

  local revision_output
  if ! revision_output=$("${dc[@]}" exec -T backend alembic current 2>&1); then
    printf 'Routine deploy refused: the running backend revision could not be verified.\n' >&2
    exit 2
  fi
  if ! CURRENT_DB_REVISION=$(extract_single_head_revision "$revision_output"); then
    printf 'Routine deploy refused: the running database revision is not a single head.\n' >&2
    exit 2
  fi
  if [[ "$CURRENT_DB_REVISION" != "20260829_0038" ]]; then
    printf 'Routine deploy refused: pricing closure migration 0038 is not the active head.\n' >&2
    exit 2
  fi
  printf '%s\n' "$CURRENT_DB_REVISION"
  if ! "${dc[@]}" exec -T backend \
    python scripts/ops/pricing_closure_readiness_cli.py audit; then
    printf 'Routine deploy refused: the current pricing closure audit did not pass.\n' >&2
    exit 2
  fi
}

verify_target_schema_head() {
  if [[ "$DRY_RUN" == "1" ]]; then
    run "${dc[@]}" run --rm --no-deps backend alembic heads
    TARGET_CODE_HEAD="$CURRENT_DB_REVISION"
    return 0
  fi

  local target_output
  if ! target_output=$("${dc[@]}" run --rm --no-deps backend alembic heads 2>&1); then
    printf 'Routine deploy refused: target Alembic head could not be verified.\n' >&2
    exit 2
  fi
  if ! TARGET_CODE_HEAD=$(extract_single_head_revision "$target_output"); then
    printf 'Routine deploy refused: target code does not contain exactly one migration head.\n' >&2
    exit 2
  fi
  if [[ "$TARGET_CODE_HEAD" != "$CURRENT_DB_REVISION" ]]; then
    printf 'Routine deploy refused: target code contains a different migration head; use the staged migration procedure.\n' >&2
    exit 2
  fi
  printf '%s\n' "$TARGET_CODE_HEAD"
}

verify_approved_checkout() {
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi

  local expected_sha actual_sha
  expected_sha=$(git rev-parse "$RELEASE_SHA^{commit}")
  actual_sha=$(git rev-parse HEAD)
  if [[ "$actual_sha" != "$expected_sha" ]]; then
    printf 'Routine deploy refused: checkout did not land on the approved SHA.\n' >&2
    exit 2
  fi
  if [[ -n "$(git status --porcelain)" ]]; then
    printf 'Routine deploy refused: checkout changed after pinning the approved SHA.\n' >&2
    exit 2
  fi
}

run_drain_gate() {
  if [[ "$DRY_RUN" == "1" ]]; then
    run "${dc[@]}" run --rm --no-deps backend \
      python scripts/ops/celery_release_gate.py drain \
      --expected-workers 3 \
      --wait-seconds "$DRAIN_TIMEOUT_SECONDS" \
      --poll-seconds "$DRAIN_POLL_SECONDS"
    return 0
  fi
  if ! "${dc[@]}" run --rm --no-deps backend \
    python scripts/ops/celery_release_gate.py drain \
    --expected-workers 3 \
    --wait-seconds "$DRAIN_TIMEOUT_SECONDS" \
    --poll-seconds "$DRAIN_POLL_SECONDS"; then
    printf 'Task drain gate failed; public ingress remains stopped and workers remain available for recovery.\n' >&2
    exit 2
  fi
}

verify_stopped_broker() {
  if [[ "$DRY_RUN" == "1" ]]; then
    run "${dc[@]}" run --rm --no-deps backend \
      python scripts/ops/celery_release_gate.py broker-only \
      --wait-seconds "$BROKER_GATE_TIMEOUT_SECONDS" \
      --poll-seconds "$DRAIN_POLL_SECONDS"
    return 0
  fi
  if ! "${dc[@]}" run --rm --no-deps backend \
    python scripts/ops/celery_release_gate.py broker-only \
    --wait-seconds "$BROKER_GATE_TIMEOUT_SECONDS" \
    --poll-seconds "$DRAIN_POLL_SECONDS"; then
    printf 'Stopped-worker broker gate failed; public ingress, backend, and workers remain stopped.\n' >&2
    exit 2
  fi
}

verify_running_revision() {
  if [[ "$DRY_RUN" == "1" ]]; then
    run "${dc[@]}" exec -T backend alembic current
    return 0
  fi

  local revision_output running_revision
  if ! revision_output=$("${dc[@]}" exec -T backend alembic current 2>&1); then
    fail_closed_after_writer_shutdown \
      'Target release revision check failed; application writers and public ingress remain stopped.'
  fi
  if ! running_revision=$(extract_single_head_revision "$revision_output") || \
    [[ "$running_revision" != "$CURRENT_DB_REVISION" ]]; then
    fail_closed_after_writer_shutdown \
      'Target release revision differs from the approved database head; application writers and public ingress remain stopped.'
  fi
  printf '%s\n' "$running_revision"
}

verify_workers() {
  if [[ "$DRY_RUN" == "1" ]]; then
    run "${dc[@]}" exec -T backend \
      python scripts/ops/celery_release_gate.py workers \
      --expected-workers 3 \
      --wait-seconds "$WORKER_READY_TIMEOUT_SECONDS" \
      --poll-seconds 5
    return 0
  fi
  if ! "${dc[@]}" exec -T backend \
    python scripts/ops/celery_release_gate.py workers \
    --expected-workers 3 \
    --wait-seconds "$WORKER_READY_TIMEOUT_SECONDS" \
    --poll-seconds 5; then
    fail_closed_after_writer_shutdown \
      'Workers failed the release health gate; application writers and public ingress remain stopped.'
  fi
}

deploy_approved_release() {
  run git fetch origin develop
  if [[ "$DRY_RUN" != "1" ]]; then
    if ! git merge-base --is-ancestor "$RELEASE_SHA" origin/develop; then
      printf 'Routine deploy refused: approved SHA is not contained in origin/develop.\n' >&2
      exit 2
    fi
  fi
  run git merge --ff-only "$RELEASE_SHA"
  verify_approved_checkout

  run "${dc[@]}" build backend worker worker-image worker-video frontend
  verify_target_schema_head
  run "${dc[@]}" stop nginx
  run_drain_gate
  run "${dc[@]}" stop -t "$STOP_GRACE_SECONDS" backend
  run_drain_gate
  run "${dc[@]}" stop -t "$STOP_GRACE_SECONDS" worker worker-image worker-video
  verify_stopped_broker
  if [[ "$DRY_RUN" == "1" ]]; then
    run "${dc[@]}" run --rm --no-deps backend \
      python scripts/ops/pricing_closure_readiness_cli.py audit
  elif ! "${dc[@]}" run --rm --no-deps backend \
    python scripts/ops/pricing_closure_readiness_cli.py audit; then
    fail_closed_after_writer_shutdown \
      'Target image audit failed; application writers and public ingress remain stopped.'
  fi
  if ! run "${dc[@]}" up -d --no-build --wait --wait-timeout 180 backend frontend; then
    fail_closed_after_writer_shutdown \
      'Target backend/frontend failed to start; application writers and public ingress remain stopped.'
  fi
  verify_running_revision

  if [[ "$DRY_RUN" == "1" ]]; then
    run "${dc[@]}" exec -T backend \
      python scripts/ops/pricing_closure_readiness_cli.py audit
  elif ! "${dc[@]}" exec -T backend \
    python scripts/ops/pricing_closure_readiness_cli.py audit; then
    fail_closed_after_writer_shutdown \
      'Target release audit failed; application writers and public ingress remain stopped.'
  fi

  if ! run "${dc[@]}" up -d --no-build --no-deps worker worker-image worker-video; then
    fail_closed_after_writer_shutdown \
      'Target workers failed to start; application writers and public ingress remain stopped.'
  fi
  verify_workers
  if ! run "${dc[@]}" up -d --no-build --no-deps nginx; then
    fail_closed_after_writer_shutdown \
      'Public ingress failed to start; application writers and public ingress remain stopped.'
  fi
  run "${dc[@]}" ps
}

routine_release_guard
deploy_approved_release
