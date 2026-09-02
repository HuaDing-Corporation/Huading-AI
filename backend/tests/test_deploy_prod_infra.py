from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA = REPO_ROOT / "infra"

_OVERALL_WAIT_KEYS_BY_EXAMPLE = {
    REPO_ROOT / "backend" / ".env.example": {
        "ENGINE_SEEDANCE_TIMEOUT_SECONDS",
        "ENGINE_OMNIHUMAN_TIMEOUT_SECONDS",
        "ENGINE_APIMART_TIMEOUT_SECONDS",
        "ENGINE_APIMART_VIDEO_TIMEOUT_SECONDS",
        "ENGINE_IMAGE_PROVIDER_TIMEOUT_SECONDS",
        "OPENAI_IMAGE_TIMEOUT",
    },
    INFRA / ".env.example": {
        "ENGINE_SEEDANCE_TIMEOUT_SECONDS",
        "ENGINE_OMNIHUMAN_TIMEOUT_SECONDS",
        "ENGINE_APIMART_TIMEOUT_SECONDS",
        "ENGINE_APIMART_VIDEO_TIMEOUT_SECONDS",
        "ENGINE_IMAGE_PROVIDER_TIMEOUT_SECONDS",
        "OPENAI_IMAGE_TIMEOUT",
    },
    INFRA / ".env.prod.example": {
        "ENGINE_SEEDANCE_TIMEOUT_SECONDS",
        "ENGINE_OMNIHUMAN_TIMEOUT_SECONDS",
        "ENGINE_APIMART_TIMEOUT_SECONDS",
        "ENGINE_APIMART_VIDEO_TIMEOUT_SECONDS",
        "ENGINE_IMAGE_PROVIDER_TIMEOUT_SECONDS",
        "OPENAI_IMAGE_TIMEOUT",
    },
}

_AIBRAIN_USER_RATE_ENV = {
    "ENGINE_AIBRAIN_LOW_INPUT_CREDITS_PER_1K": "1.12",
    "ENGINE_AIBRAIN_LOW_OUTPUT_CREDITS_PER_1K": "6.72",
    "ENGINE_AIBRAIN_LOW_ABOVE_272K_INPUT_CREDITS_PER_1K": "2.24",
    "ENGINE_AIBRAIN_LOW_ABOVE_272K_OUTPUT_CREDITS_PER_1K": "10.08",
    "ENGINE_AIBRAIN_MID_INPUT_CREDITS_PER_1K": "2.80",
    "ENGINE_AIBRAIN_MID_OUTPUT_CREDITS_PER_1K": "16.80",
    "ENGINE_AIBRAIN_MID_ABOVE_272K_INPUT_CREDITS_PER_1K": "5.60",
    "ENGINE_AIBRAIN_MID_ABOVE_272K_OUTPUT_CREDITS_PER_1K": "25.20",
    "ENGINE_AIBRAIN_HIGH_INPUT_CREDITS_PER_1K": "5.60",
    "ENGINE_AIBRAIN_HIGH_OUTPUT_CREDITS_PER_1K": "33.60",
    "ENGINE_AIBRAIN_HIGH_ABOVE_272K_INPUT_CREDITS_PER_1K": "11.20",
    "ENGINE_AIBRAIN_HIGH_ABOVE_272K_OUTPUT_CREDITS_PER_1K": "50.40",
}


def _prod_compose() -> dict:
    return yaml.safe_load((INFRA / "docker-compose.prod.yml").read_text(encoding="utf-8"))


def _nginx_location_block(conf: str, location: str) -> str:
    marker = f"location {location} {{"
    start = conf.index(marker)
    brace_depth = 0
    for index in range(start, len(conf)):
        char = conf[index]
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth -= 1
            if brace_depth == 0:
                return conf[start : index + 1]
    raise AssertionError(f"location block not closed: {location}")


def _worker_queue(command: str) -> str:
    args = shlex.split(command)
    return args[args.index("-Q") + 1]


def _env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key and not key.startswith("#"):
            values[key] = value
    return values


def test_generation_overall_waits_are_1500_in_every_env_example() -> None:
    for path, required_keys in _OVERALL_WAIT_KEYS_BY_EXAMPLE.items():
        values = _env_values(path)
        assert required_keys <= values.keys(), path
        assert {key: values[key] for key in required_keys} == {key: "1500" for key in required_keys}


def test_prod_env_uses_calibrated_apimart_exchange_rate() -> None:
    values = _env_values(INFRA / ".env.prod.example")

    assert values["ENGINE_APIMART_CREDIT_USD"] == "0.10"
    assert values["ENGINE_USD_CNY_RATE"] == "7.0"


def test_prod_env_enables_production_gate_without_committing_official_voice_ids() -> None:
    """A local mode or repository-owned inventory would bypass the signed release handoff."""
    values = _env_values(INFRA / ".env.prod.example")

    assert values["ENVIRONMENT"] == "production"
    assert values["ENGINE_DOUBAO_OFFICIAL_VOICE_IDS"] == ""


def test_aibrain_user_rate_tiers_are_complete_in_env_examples_and_runbook() -> None:
    for path in _OVERALL_WAIT_KEYS_BY_EXAMPLE:
        values = _env_values(path)
        assert {key: values.get(key) for key in _AIBRAIN_USER_RATE_ENV} == (
            _AIBRAIN_USER_RATE_ENV
        ), path

    deploy_doc = (INFRA / "DEPLOY.md").read_text(encoding="utf-8")
    for key, value in _AIBRAIN_USER_RATE_ENV.items():
        assert f"{key}={value}" in deploy_doc
    assert "will not pick up new example values automatically" in deploy_doc


def test_prod_compose_exposes_only_nginx_and_persists_state() -> None:
    compose = _prod_compose()
    services = compose["services"]

    assert {
        "frontend",
        "backend",
        "worker",
        "worker-image",
        "postgres",
        "redis",
        "minio",
        "minio-init",
        "nginx",
        "certbot",
    } <= set(services)

    assert services["nginx"]["ports"] == ["80:80", "443:443"]
    for internal in ["postgres", "redis", "minio", "backend", "frontend"]:
        assert "ports" not in services[internal]

    volumes = set(compose["volumes"])
    assert {"postgres-data", "redis-data", "minio-data", "certbot-etc", "certbot-www"} <= volumes
    assert services["postgres"]["restart"] == "always"
    assert services["redis"]["restart"] == "always"
    assert services["minio"]["restart"] == "always"


def test_prod_minio_init_uses_single_argv_command_and_no_dead_cors() -> None:
    minio_init = _prod_compose()["services"]["minio-init"]

    assert minio_init["entrypoint"] == ["/bin/sh", "-c"]
    command = minio_init["command"]
    # A scalar is word-split by Compose, so sh -c runs only `set` and creates no bucket.
    assert isinstance(command, list)
    assert len(command) == 1
    script = command[0]
    assert "mc mb --ignore-existing" in script
    assert "mc anonymous set none" in script
    assert "cors" not in script.lower()
    assert "ENGINE_CORS_ORIGINS" not in minio_init["environment"]


def test_frontend_runtime_image_uses_standalone_multistage_build() -> None:
    dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    from_lines = [line.strip() for line in dockerfile.splitlines() if line.startswith("FROM ")]

    assert from_lines == [
        "FROM node:20-slim AS builder",
        "FROM node:20-slim AS runner",
    ]
    assert "ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000" in dockerfile
    assert "ARG NEXT_PUBLIC_USE_MOCK=0" in dockerfile
    runner = dockerfile.split("FROM node:20-slim AS runner", maxsplit=1)[1]
    assert "pnpm install" not in runner
    assert "corepack enable" not in runner
    assert "COPY --from=builder /repo/frontend/.next/standalone ./" in runner
    assert "COPY --from=builder /repo/frontend/.next/static ./frontend/.next/static" in runner
    assert 'CMD ["node", "server.js"]' in runner

    next_config = (REPO_ROOT / "frontend" / "next.config.ts").read_text(encoding="utf-8")
    assert 'output: "standalone"' in next_config


def test_prod_compose_wires_public_frontend_and_backend_env() -> None:
    compose = _prod_compose()
    backend_env = compose["services"]["backend"]["environment"]
    worker_env = compose["services"]["worker"]["environment"]
    frontend_args = compose["services"]["frontend"]["build"]["args"]

    assert backend_env["ENGINE_CORS_ORIGINS"] == "https://huadingai.cn"
    assert backend_env["ENGINE_S3_PUBLIC_ENDPOINT"] == "https://huadingai.cn"
    assert worker_env["ENGINE_S3_PUBLIC_ENDPOINT"] == "https://huadingai.cn"
    assert frontend_args["NEXT_PUBLIC_API_BASE_URL"] == "https://huadingai.cn/api"
    assert frontend_args["NEXT_PUBLIC_USE_MOCK"] == "0"
    assert compose["services"]["worker"]["command"].endswith("-Q default,avatar --loglevel=info")


def test_prod_backend_can_disable_startup_migrations_for_routine_releases() -> None:
    backend = _prod_compose()["services"]["backend"]
    env_values = _env_values(INFRA / ".env.prod.example")

    assert backend["environment"]["BACKEND_AUTO_MIGRATE"] == "${BACKEND_AUTO_MIGRATE:-0}"
    assert env_values["BACKEND_AUTO_MIGRATE"] == "0"
    assert (
        'if [ "$$BACKEND_AUTO_MIGRATE" = "1" ]; then alembic upgrade head; fi' in backend["command"]
    )


def test_prod_workers_isolate_image_concurrency_from_avatar() -> None:
    services = _prod_compose()["services"]
    worker = services["worker"]
    image_worker = services["worker-image"]
    video_worker = services["worker-video"]

    worker_args = shlex.split(worker["command"])
    assert "--pool=solo" in worker_args
    assert "--concurrency=1" in worker_args
    assert _worker_queue(worker["command"]) == "default,avatar"

    image_args = shlex.split(image_worker["command"])
    assert "--pool=prefork" in image_args
    assert "--concurrency=3" in image_args
    assert _worker_queue(image_worker["command"]) == "image"

    assert _worker_queue(video_worker["command"]) == "video"
    assert worker["environment"] == image_worker["environment"]
    assert worker["depends_on"] == image_worker["depends_on"]
    assert worker["volumes"] == image_worker["volumes"]


def test_prod_nginx_enforces_https_and_supports_api_sse_and_minio() -> None:
    nginx_conf = (INFRA / "nginx" / "conf.d" / "huadingai.conf").read_text(encoding="utf-8")

    assert "return 301 https://$host$request_uri;" in nginx_conf
    assert "ssl_certificate /etc/letsencrypt/live/huadingai.cn/fullchain.pem;" in nginx_conf
    assert "resolver 127.0.0.11 valid=10s ipv6=off;" in nginx_conf
    assert "set $upstream_backend http://backend:8000;" in nginx_conf
    assert "set $upstream_frontend http://frontend:3000;" in nginx_conf
    assert "set $upstream_minio http://minio:9000;" in nginx_conf
    assert "proxy_pass $upstream_backend;" in nginx_conf
    assert "proxy_pass $upstream_frontend;" in nginx_conf
    assert "location ~ ^/api/.*/events$" in nginx_conf
    events_block = _nginx_location_block(nginx_conf, "~ ^/api/.*/events$")
    assert "proxy_buffering off;" in events_block
    assert "proxy_read_timeout 1800s;" in events_block
    assert "proxy_send_timeout 1800s;" in events_block
    api_block = _nginx_location_block(nginx_conf, "/api/")
    assert "proxy_set_header Host $host;" in api_block
    assert "proxy_read_timeout 120s;" in api_block
    assert "proxy_send_timeout 120s;" in api_block
    assert "location /minio/" not in nginx_conf
    minio_block = _nginx_location_block(nginx_conf, "/huading-videos/")
    assert "proxy_set_header Host $host;" in minio_block
    assert "proxy_hide_header Access-Control-Allow-Origin;" in minio_block
    assert (
        'add_header Access-Control-Expose-Headers "ETag, Content-Length, Content-Type" always;'
        in minio_block
    )
    assert "proxy_pass $upstream_minio;" in minio_block
    assert "proxy_pass $upstream_minio/;" not in minio_block


def test_nginx_accepts_4096_byte_quote_header() -> None:
    nginx_conf = (INFRA / "nginx" / "conf.d" / "huadingai.conf").read_text(encoding="utf-8")
    buffers = next(
        line for line in nginx_conf.splitlines() if "large_client_header_buffers" in line
    )
    buffer_size = buffers.rstrip(";").split()[-1].lower()
    assert int(buffer_size.removesuffix("k")) * 1024 >= 16 * 1024


def test_backend_uv_lock_uses_official_sources() -> None:
    lock = (REPO_ROOT / "backend" / "uv.lock").read_text(encoding="utf-8")
    pyproject = (REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")

    assert "pypi.tuna.tsinghua.edu.cn" not in lock
    assert 'registry = "https://pypi.org/simple"' in lock
    assert "https://files.pythonhosted.org/packages" in lock
    assert 'index-url = "https://pypi.org/simple"' in pyproject


def test_prod_env_example_and_runbook_have_placeholders_only() -> None:
    env_example = (INFRA / ".env.prod.example").read_text(encoding="utf-8")
    backend_env_example = (REPO_ROOT / "backend" / ".env.example").read_text(encoding="utf-8")
    deploy_doc = (INFRA / "DEPLOY.md").read_text(encoding="utf-8")

    required_keys = [
        "JWT_SECRET_KEY=",
        "DATABASE_URL=",
        "REDIS_URL=",
        "ENGINE_CORS_ORIGINS=https://huadingai.cn",
        "ENGINE_S3_ENDPOINT=http://minio:9000",
        "ENGINE_S3_PUBLIC_ENDPOINT=https://huadingai.cn",
        "ENGINE_OMNIHUMAN_ACCESS_KEY=",
        "ENGINE_OMNIHUMAN_SECRET_KEY=",
        "ENGINE_SEEDANCE_API_KEY=",
        "ENGINE_SEEDANCE_MINI_MODEL=",
        "ENGINE_APIMART_VIDEO_MODEL=doubao-seedance-2.0",
        "ENGINE_APIMART_VIDEO_POLL_INITIAL_DELAY_SECONDS=30",
        "ENGINE_APIMART_VIDEO_POLL_INTERVAL_SECONDS=10",
        "ENGINE_APIMART_VIDEO_TIMEOUT_SECONDS=1500",
        "ENGINE_ORPHAN_TASK_STALE_SECONDS=1800",
        "ENGINE_ORPHAN_RECOVERY_INTERVAL_SECONDS=60",
        "ENGINE_DOUBAO_TTS_APPID=",
        "ENGINE_DOUBAO_VOICE_CLONE_APPID=",
        "OPENAI_API_KEY=",
        "DEEPSEEK_API_KEY=",
        "NEXT_PUBLIC_API_BASE_URL=https://huadingai.cn/api",
        "NEXT_PUBLIC_USE_MOCK=0",
    ]
    for key in required_keys:
        assert key in env_example

    backend_required_keys = [
        "ENGINE_APIMART_VIDEO_MODEL=doubao-seedance-2.0",
        "ENGINE_APIMART_VIDEO_POLL_INITIAL_DELAY_SECONDS=30",
        "ENGINE_APIMART_VIDEO_POLL_INTERVAL_SECONDS=10",
        "ENGINE_APIMART_VIDEO_TIMEOUT_SECONDS=1500",
        "ENGINE_ORPHAN_TASK_STALE_SECONDS=1800",
        "ENGINE_ORPHAN_RECOVERY_INTERVAL_SECONDS=60",
    ]
    for key in backend_required_keys:
        assert key in backend_env_example

    assert "video_gen presigned reference URLs use a 7200 second minimum TTL" in env_example
    assert "ENGINE_S3_PRESIGN_TTL=3600" in env_example
    assert "sk-" not in env_example
    assert "task-" not in env_example
    assert "sk-" not in backend_env_example
    assert "task-" not in backend_env_example
    assert "https://huadingai.cn/minio" not in env_example
    assert "bash infra/deploy.sh" in deploy_doc
    assert "certbot certonly --webroot" in deploy_doc
    assert "ENGINE_APIMART_VIDEO_MODEL" in deploy_doc
    assert "video_gen" in deploy_doc
    assert "https://huadingai.cn/huading-videos/" in deploy_doc
    assert "https://huadingai.cn/minio" not in deploy_doc


def test_deploy_runbook_requires_existing_cost_rate_env_migration() -> None:
    deploy_doc = (INFRA / "DEPLOY.md").read_text(encoding="utf-8")

    assert "ENGINE_APIMART_CREDIT_USD=0.10" in deploy_doc
    assert "ENGINE_USD_CNY_RATE=7.0" in deploy_doc
    assert "does not overwrite an existing `infra/.env`" in deploy_doc


def test_pricing_closure_runbook_keeps_all_writers_stopped_through_registration() -> None:
    """The backup and both audits must describe one write-free maintenance window."""
    deploy_doc = (INFRA / "DEPLOY.md").read_text(encoding="utf-8")
    release = deploy_doc.split(
        "## Existing Production: Pricing Closure Staged Release", maxsplit=1
    )[1]

    build = release.index("build backend worker worker-image worker-video frontend")
    close_ingress = release.index("stop nginx", build)
    first_drain = release.index("celery_release_gate drain", close_ingress)
    stop_backend = release.index("stop -t 1800 backend", first_drain)
    second_drain = release.index("celery_release_gate drain", stop_backend)
    stop_workers = release.index(
        "stop -t 1800 worker worker-image worker-video",
        second_drain,
    )
    stopped_broker_gate = release.index("celery_release_gate broker-only", stop_workers)
    final_backup = release.index("final write-free PostgreSQL", stopped_broker_gate)
    preflight = release.index("pricing_closure_readiness_cli.py preflight", final_backup)
    migrate = release.index("backend alembic upgrade head", preflight)
    first_audit = release.index("pricing_closure_readiness_cli.py audit", migrate)
    register = release.index("pricing_closure_readiness_cli.py register-official", first_audit)
    second_audit = release.index("pricing_closure_readiness_cli.py audit", register)
    start_backend = release.index("up -d --no-build backend frontend", second_audit)
    assert "BACKEND_AUTO_MIGRATE=0" in release[second_audit:start_backend]
    start_workers = release.index(
        "up -d --no-build --no-deps worker worker-image worker-video", start_backend
    )
    open_ingress = release.index("up -d --no-build --no-deps nginx", start_workers)
    ordered_steps = (
        build,
        close_ingress,
        first_drain,
        stop_backend,
        second_drain,
        stop_workers,
        stopped_broker_gate,
        final_backup,
        preflight,
        migrate,
        first_audit,
        register,
        second_audit,
        start_backend,
        start_workers,
        open_ingress,
    )

    assert list(ordered_steps) == sorted(ordered_steps)
    assert "up -d --build" not in release
    assert release.count("build backend worker worker-image worker-video frontend") == 1
    assert "build backend" not in release[final_backup:preflight]


def test_chinese_pricing_gate_uses_the_same_write_free_release_order() -> None:
    gate = (REPO_ROOT / "docs" / "02-方案设计" / "定价闭环生产上线门禁.md").read_text(
        encoding="utf-8"
    )

    build = gate.index("build backend worker worker-image worker-video frontend")
    close_ingress = gate.index("stop nginx", build)
    first_drain = gate.index("celery_release_gate drain", close_ingress)
    stop_backend = gate.index("stop -t 1800 backend", first_drain)
    second_drain = gate.index("celery_release_gate drain", stop_backend)
    stop_workers = gate.index(
        "stop -t 1800 worker worker-image worker-video",
        second_drain,
    )
    stopped_broker_gate = gate.index("celery_release_gate broker-only", stop_workers)
    final_backup = gate.index("最终静默备份", stopped_broker_gate)
    preflight = gate.index("pricing_closure_readiness_cli.py preflight", final_backup)
    migrate = gate.index("backend alembic upgrade head", preflight)
    first_audit = gate.index("pricing_closure_readiness_cli.py audit", migrate)
    register = gate.index("pricing_closure_readiness_cli.py register-official", first_audit)
    second_audit = gate.index("pricing_closure_readiness_cli.py audit", register)
    start_backend = gate.index("up -d --no-build backend frontend", second_audit)
    assert "BACKEND_AUTO_MIGRATE=0" in gate[second_audit:start_backend]
    start_workers = gate.index(
        "up -d --no-build --no-deps worker worker-image worker-video", start_backend
    )
    open_ingress = gate.index("up -d --no-build --no-deps nginx", start_workers)
    ordered_steps = (
        build,
        close_ingress,
        first_drain,
        stop_backend,
        second_drain,
        stop_workers,
        stopped_broker_gate,
        final_backup,
        preflight,
        migrate,
        first_audit,
        register,
        second_audit,
        start_backend,
        start_workers,
        open_ingress,
    )

    assert list(ordered_steps) == sorted(ordered_steps)
    assert "部署后端、前端与迁移" not in gate


def test_deploy_shortcut_is_scoped_away_from_first_pricing_closure_rollout() -> None:
    deploy_doc = (INFRA / "DEPLOY.md").read_text(encoding="utf-8")
    first_start = deploy_doc.split("## First Start", maxsplit=1)[1].split("## Verify", maxsplit=1)[
        0
    ]
    operations = deploy_doc.split("## Operations", maxsplit=1)[1].split("## Notes", maxsplit=1)[0]

    assert "bash infra/deploy.sh" not in first_start
    assert "bootstrap-empty-preflight" in first_start
    assert "legacy `preflight`" in first_start
    assert "Never use `infra/deploy.sh` for first startup" in first_start
    assert "existing `0031` database" in first_start
    assert operations.index("pricing-closure migration and registry gate") < (
        operations.index("bash infra/deploy.sh --routine --release-sha <approved-sha>")
    )
    assert "post-registration audit has already passed" in operations


def test_runbooks_require_target_audit_queue_discovery_and_full_fail_closed_cleanup() -> None:
    deploy_doc = (INFRA / "DEPLOY.md").read_text(encoding="utf-8")
    operations = deploy_doc.split("## Operations", maxsplit=1)[1].split("## Notes", maxsplit=1)[0]
    broker_gate = operations.index("celery_release_gate broker-only")
    target_audit = operations.index("pricing_closure_readiness_cli.py audit", broker_gate)
    target_start = operations.index("target backend/frontend", target_audit)

    assert broker_gate < target_audit < target_start
    assert "unapproved non-empty logical queue" in deploy_doc
    assert "backend/frontend, worker, or nginx Compose start" in deploy_doc
    assert "stop nginx, backend, frontend, and all workers" in deploy_doc

    chinese_gate = (REPO_ROOT / "docs" / "02-方案设计" / "定价闭环生产上线门禁.md").read_text(
        encoding="utf-8"
    )
    routine = chinese_gate.split("完成首次闭环后的日常 routine 发布", maxsplit=1)[1].split(
        "### 5.1", maxsplit=1
    )[0]
    assert "目标镜像 one-off 完整 `audit`" in routine
    assert "任何未批准非空逻辑队列" in chinese_gate
    assert "显式停止 nginx、backend、frontend 与全部 worker" in chinese_gate


def test_deploy_script_runs_one_command_deploy_sequence() -> None:
    script = (INFRA / "deploy.sh").read_text(encoding="utf-8")
    deploy_body = script.split("deploy_approved_release() {", maxsplit=1)[1].split(
        "\nroutine_release_guard", maxsplit=1
    )[0]

    assert 'DEPLOY_MODE=""' in script
    assert "--bootstrap-empty" not in script
    assert "--routine" in script
    assert "--release-sha" in script
    assert "A deploy mode is required" in script
    routine_revision_guard = script.index("exec -T backend alembic current")
    current_registry_guard = script.index(
        "python scripts/ops/pricing_closure_readiness_cli.py audit",
        routine_revision_guard,
    )
    fetch = deploy_body.index("git fetch origin develop")
    pin_release = deploy_body.index('git merge --ff-only "$RELEASE_SHA"', fetch)
    clean_release = deploy_body.index("verify_approved_checkout", pin_release)
    build = deploy_body.index(
        "build backend worker worker-image worker-video frontend",
        clean_release,
    )
    target_head = deploy_body.index("verify_target_schema_head", build)
    close_ingress = deploy_body.index("stop nginx", target_head)
    first_drain = deploy_body.index("run_drain_gate", close_ingress)
    stop_backend = deploy_body.index('stop -t "$STOP_GRACE_SECONDS" backend', first_drain)
    second_drain = deploy_body.index("run_drain_gate", stop_backend)
    stop_workers = deploy_body.index(
        'stop -t "$STOP_GRACE_SECONDS" worker worker-image worker-video', second_drain
    )
    stopped_broker_gate = deploy_body.index("verify_stopped_broker", stop_workers)
    start_backend = deploy_body.index("up -d --no-build --wait", stopped_broker_gate)
    target_revision = deploy_body.index("verify_running_revision", start_backend)
    target_registry_audit = deploy_body.index(
        "python scripts/ops/pricing_closure_readiness_cli.py audit",
        target_revision,
    )
    start_workers = deploy_body.index(
        "up -d --no-build --no-deps worker worker-image worker-video",
        target_registry_audit,
    )
    worker_gate = deploy_body.index("verify_workers", start_workers)
    open_ingress = deploy_body.index("up -d --no-build --no-deps nginx", worker_gate)
    assert [
        fetch,
        pin_release,
        clean_release,
        build,
        target_head,
        close_ingress,
        first_drain,
        stop_backend,
        second_drain,
        stop_workers,
        stopped_broker_gate,
        start_backend,
        target_revision,
        target_registry_audit,
        start_workers,
        worker_gate,
        open_ingress,
    ] == sorted(
        [
            fetch,
            pin_release,
            clean_release,
            build,
            target_head,
            close_ingress,
            first_drain,
            stop_backend,
            second_drain,
            stop_workers,
            stopped_broker_gate,
            start_backend,
            target_revision,
            target_registry_audit,
            start_workers,
            worker_gate,
            open_ingress,
        ]
    )
    assert routine_revision_guard < current_registry_guard
    assert "up -d --build" not in script
    assert "restart nginx" not in script
    assert "${DRY_RUN:-0}" in script
    assert "BACKEND_AUTO_MIGRATE=0" in script
    assert 'BROKER_GATE_TIMEOUT_SECONDS="${BROKER_GATE_TIMEOUT_SECONDS:-300}"' in script
    assert "checkout changed after pinning the approved SHA" in script


def test_routine_deploy_audits_the_target_image_before_starting_any_writer() -> None:
    """The target backend must prove readiness before startup recovery can write."""
    script = (INFRA / "deploy.sh").read_text(encoding="utf-8")
    deploy_body = script.split("deploy_approved_release() {", maxsplit=1)[1].split(
        "\nroutine_release_guard", maxsplit=1
    )[0]

    stopped_broker_gate = deploy_body.index("verify_stopped_broker")
    target_audit = deploy_body.index(
        "python scripts/ops/pricing_closure_readiness_cli.py audit",
        stopped_broker_gate,
    )
    start_backend = deploy_body.index("up -d --no-build --wait", stopped_broker_gate)
    running_revision = deploy_body.index("verify_running_revision", start_backend)
    post_start_audit = deploy_body.index(
        "python scripts/ops/pricing_closure_readiness_cli.py audit",
        running_revision,
    )

    assert target_audit < start_backend < running_revision < post_start_audit
    assert "run --rm --no-deps backend" in deploy_body[stopped_broker_gate:target_audit]
    assert "exec -T backend" in deploy_body[running_revision:post_start_audit]


@pytest.mark.parametrize(
    "failure",
    (
        "backend-frontend-up",
        "revision",
        "post-start-audit",
        "workers-up",
        "worker-probe",
        "nginx-up",
    ),
)
def test_routine_deploy_failure_after_writer_shutdown_cleans_every_application_service(
    tmp_path: Path,
    failure: str,
) -> None:
    """Partial Compose starts must never leave a writer or public ingress running."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        """#!/usr/bin/env bash
if [[ "$1" == "branch" && "$2" == "--show-current" ]]; then
  printf 'develop\\n'
  exit 0
fi
if [[ "$1" == "status" && "$2" == "--porcelain" ]]; then
  exit 0
fi
if [[ "$1" == "rev-parse" ]]; then
  printf '0123456\\n'
  exit 0
fi
if [[ "$1" == "fetch" || "$1" == "merge" || "$1" == "merge-base" ]]; then
  exit 0
fi
exit 99
""",
        encoding="utf-8",
    )
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
joined=" $* "
backend_frontend_up=" up -d --no-build --wait --wait-timeout 180 backend frontend "
workers_up=" up -d --no-build --no-deps worker worker-image worker-video "
printf '%s\\n' "$*" >>"$FAKE_DOCKER_LOG"

if [[ "$joined" == *" alembic current "* ]]; then
  current_count=0
  if [[ -f "$FAKE_CURRENT_COUNT_FILE" ]]; then
    current_count=$(<"$FAKE_CURRENT_COUNT_FILE")
  fi
  current_count=$((current_count + 1))
  printf '%s\\n' "$current_count" >"$FAKE_CURRENT_COUNT_FILE"
  if [[ "$FAKE_FAILURE" == "revision" && "$current_count" == "2" ]]; then
    exit 1
  fi
  printf '20260829_0038 (head)\\n'
  exit 0
fi
if [[ "$joined" == *" alembic heads "* ]]; then
  printf '20260829_0038 (head)\\n'
  exit 0
fi
if [[ "$joined" == *"pricing_closure_readiness_cli.py audit "* ]]; then
  audit_count=0
  if [[ -f "$FAKE_AUDIT_COUNT_FILE" ]]; then
    audit_count=$(<"$FAKE_AUDIT_COUNT_FILE")
  fi
  audit_count=$((audit_count + 1))
  printf '%s\\n' "$audit_count" >"$FAKE_AUDIT_COUNT_FILE"
  if [[ "$FAKE_FAILURE" == "post-start-audit" && "$audit_count" == "3" ]]; then
    exit 1
  fi
  exit 0
fi
if [[ "$joined" == *"$backend_frontend_up"* && "$FAKE_FAILURE" == "backend-frontend-up" ]]; then
  exit 1
fi
if [[ "$joined" == *"$workers_up"* && "$FAKE_FAILURE" == "workers-up" ]]; then
  exit 1
fi
if [[ "$joined" == *"celery_release_gate.py workers "* && "$FAKE_FAILURE" == "worker-probe" ]]; then
  exit 1
fi
if [[ "$joined" == *" up -d --no-build --no-deps nginx "* && "$FAKE_FAILURE" == "nginx-up" ]]; then
  exit 1
fi
if [[ "$joined" == *" stop frontend "* ]]; then
  exit 1
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    fake_docker.chmod(0o755)

    child_env = os.environ.copy()
    child_env["FAKE_FAILURE"] = failure
    child_env["FAKE_CURRENT_COUNT_FILE"] = str(tmp_path / "current-count")
    child_env["FAKE_AUDIT_COUNT_FILE"] = str(tmp_path / "audit-count")
    docker_log = tmp_path / "docker.log"
    child_env["FAKE_DOCKER_LOG"] = str(docker_log)
    bash = "bash"
    command = [bash, "infra/deploy.sh", "--routine", "--release-sha", "0123456"]
    if os.name == "nt":
        git_executable = shutil.which("git")
        assert git_executable is not None
        bash = str(Path(git_executable).parents[1] / "bin" / "bash.exe")
        fake_bin_posix = f"/{fake_bin.drive[0].lower()}{fake_bin.as_posix()[2:]}"
        command = [
            bash,
            "-c",
            'PATH="$1:$PATH"; export PATH; bash infra/deploy.sh --routine --release-sha 0123456',
            "deploy-fail-closed-test",
            fake_bin_posix,
        ]
    else:
        child_env["PATH"] = f"{fake_bin}{os.pathsep}{child_env['PATH']}"

    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 2
    commands = docker_log.read_text(encoding="utf-8").splitlines()
    cleanup_start = max(
        index for index, command_line in enumerate(commands) if "stop nginx" in command_line
    )
    cleanup = "\n".join(commands[cleanup_start:])
    assert "stop nginx" in cleanup
    assert "stop -t 1800 backend" in cleanup
    assert "stop frontend" in cleanup
    assert "stop -t 1800 worker worker-image worker-video" in cleanup


def test_routine_deploy_refuses_migrations_rechecks_broker_and_opens_ingress_last() -> None:
    """Routine releases must preserve tasks and must never auto-migrate the database."""
    script = (INFRA / "deploy.sh").read_text(encoding="utf-8")
    deploy_body = script.split("deploy_approved_release() {", maxsplit=1)[1].split(
        "\nroutine_release_guard", maxsplit=1
    )[0]

    assert "run --rm --no-deps backend alembic heads" in script
    assert '"$TARGET_CODE_HEAD" != "$CURRENT_DB_REVISION"' in script
    build = deploy_body.index("build backend worker worker-image worker-video frontend")
    target_head = deploy_body.index("verify_target_schema_head", build)
    close_ingress = deploy_body.index("stop nginx", target_head)
    first_drain = deploy_body.index("run_drain_gate", close_ingress)
    stop_backend = deploy_body.index('stop -t "$STOP_GRACE_SECONDS" backend', first_drain)
    second_drain = deploy_body.index("run_drain_gate", stop_backend)
    stop_workers = deploy_body.index(
        'stop -t "$STOP_GRACE_SECONDS" worker worker-image worker-video',
        second_drain,
    )
    stopped_broker_gate = deploy_body.index("verify_stopped_broker", stop_workers)
    start_backend = deploy_body.index("up -d --no-build --wait", stopped_broker_gate)
    start_workers = deploy_body.index(
        "up -d --no-build --no-deps worker worker-image worker-video",
        start_backend,
    )
    worker_gate = deploy_body.index("verify_workers", start_workers)
    open_ingress = deploy_body.index("up -d --no-build --no-deps nginx", worker_gate)

    assert [
        build,
        target_head,
        close_ingress,
        first_drain,
        stop_backend,
        second_drain,
        stop_workers,
        stopped_broker_gate,
        start_backend,
        start_workers,
        worker_gate,
        open_ingress,
    ] == sorted(
        [
            build,
            target_head,
            close_ingress,
            first_drain,
            stop_backend,
            second_drain,
            stop_workers,
            stopped_broker_gate,
            start_backend,
            start_workers,
            worker_gate,
            open_ingress,
        ]
    )
    assert "Routine deploy refused: target code contains a different migration head" in script
    assert "celery_release_gate.py broker-only" in script
    assert "Stopped-worker broker gate failed" in script
    drain_gate = script.split("run_drain_gate() {", maxsplit=1)[1].split("\n}", maxsplit=1)[0]
    assert "fail_closed_after_writer_shutdown" not in drain_gate
    assert "workers remain available for recovery" in drain_gate
    assert (
        "Workers failed the release health gate; application writers and public ingress "
        "remain stopped"
        in script
    )


def test_failed_alembic_output_is_redacted_from_routine_deploy_streams(tmp_path: Path) -> None:
    """A failed Alembic subprocess must not disclose captured connection details."""
    private_marker = "postgresql://release-user:synthetic-secret@private-db.invalid/pricing"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        """#!/usr/bin/env bash
if [[ "$1" == "branch" && "$2" == "--show-current" ]]; then
  printf 'develop\\n'
  exit 0
fi
if [[ "$1" == "status" && "$2" == "--porcelain" ]]; then
  exit 0
fi
exit 99
""",
        encoding="utf-8",
    )
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' '{private_marker}'
printf '%s\\n' '{private_marker}' >&2
exit 1
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    fake_docker.chmod(0o755)
    child_env = os.environ.copy()
    bash = "bash"
    command = [bash, "infra/deploy.sh", "--routine", "--release-sha", "0123456"]
    if os.name == "nt":
        git_executable = shutil.which("git")
        assert git_executable is not None
        bash = str(Path(git_executable).parents[1] / "bin" / "bash.exe")
        fake_bin_posix = f"/{fake_bin.drive[0].lower()}{fake_bin.as_posix()[2:]}"
        command = [
            bash,
            "-c",
            'PATH="$1:$PATH"; export PATH; bash infra/deploy.sh --routine --release-sha 0123456',
            "deploy-redaction-test",
            fake_bin_posix,
        ]
    else:
        child_env["PATH"] = f"{fake_bin}{os.pathsep}{child_env['PATH']}"

    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 2
    assert private_marker not in result.stdout
    assert private_marker not in result.stderr
    assert "running backend revision could not be verified" in result.stderr


@pytest.mark.parametrize(
    "scenario",
    ["routine-current-mismatch", "target-head-mismatch", "running-current-mismatch"],
)
def test_successful_alembic_semantic_mismatch_never_emits_revision(
    tmp_path: Path,
    scenario: str,
) -> None:
    """A parsed but unapproved revision is private failure detail, not success output."""
    private_revision = f"synthetic_private_{scenario.replace('-', '_')}"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        """#!/usr/bin/env bash
if [[ "$1" == "branch" && "$2" == "--show-current" ]]; then
  printf 'develop\\n'
  exit 0
fi
if [[ "$1" == "status" && "$2" == "--porcelain" ]]; then
  exit 0
fi
if [[ "$1" == "rev-parse" ]]; then
  printf '0123456\\n'
  exit 0
fi
if [[ "$1" == "fetch" || "$1" == "merge" || "$1" == "merge-base" ]]; then
  exit 0
fi
exit 99
""",
        encoding="utf-8",
    )
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
joined=" $* "
if [[ "$joined" == *" alembic current "* ]]; then
  current_count=0
  if [[ -f "$FAKE_CURRENT_COUNT_FILE" ]]; then
    current_count=$(<"$FAKE_CURRENT_COUNT_FILE")
  fi
  current_count=$((current_count + 1))
  printf '%s\\n' "$current_count" >"$FAKE_CURRENT_COUNT_FILE"
  if [[ "$FAKE_SCENARIO" == "routine-current-mismatch" && "$current_count" == "1" ]]; then
    printf '%s (head)\\n' "$FAKE_PRIVATE_REVISION"
  elif [[ "$FAKE_SCENARIO" == "running-current-mismatch" && "$current_count" == "2" ]]; then
    printf '%s (head)\\n' "$FAKE_PRIVATE_REVISION"
  else
    printf '20260829_0038 (head)\\n'
  fi
  exit 0
fi
if [[ "$joined" == *" alembic heads "* ]]; then
  if [[ "$FAKE_SCENARIO" == "target-head-mismatch" ]]; then
    printf '%s (head)\\n' "$FAKE_PRIVATE_REVISION"
  else
    printf '20260829_0038 (head)\\n'
  fi
  exit 0
fi
printf '%s\\n' "$*" >>"$FAKE_DOCKER_LOG"
exit 0
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    fake_docker.chmod(0o755)
    child_env = os.environ.copy()
    child_env["FAKE_SCENARIO"] = scenario
    child_env["FAKE_PRIVATE_REVISION"] = private_revision
    child_env["FAKE_CURRENT_COUNT_FILE"] = str(tmp_path / "current-count")
    docker_log = tmp_path / "docker.log"
    child_env["FAKE_DOCKER_LOG"] = str(docker_log)
    bash = "bash"
    command = [bash, "infra/deploy.sh", "--routine", "--release-sha", "0123456"]
    if os.name == "nt":
        git_executable = shutil.which("git")
        assert git_executable is not None
        bash = str(Path(git_executable).parents[1] / "bin" / "bash.exe")
        fake_bin_posix = f"/{fake_bin.drive[0].lower()}{fake_bin.as_posix()[2:]}"
        command = [
            bash,
            "-c",
            'PATH="$1:$PATH"; export PATH; bash infra/deploy.sh --routine --release-sha 0123456',
            "deploy-semantic-mismatch-test",
            fake_bin_posix,
        ]
    else:
        child_env["PATH"] = f"{fake_bin}{os.pathsep}{child_env['PATH']}"

    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 2
    assert private_revision not in result.stdout
    assert private_revision not in result.stderr
    if scenario == "running-current-mismatch":
        cleanup = docker_log.read_text(encoding="utf-8")
        assert "stop nginx" in cleanup
        assert "stop -t 1800 backend" in cleanup
        assert "stop frontend" in cleanup
        assert "stop -t 1800 worker worker-image worker-video" in cleanup
