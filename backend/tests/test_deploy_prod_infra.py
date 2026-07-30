from __future__ import annotations

import shlex
from pathlib import Path

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
        assert {key: values[key] for key in required_keys} == {
            key: "1500" for key in required_keys
        }


def test_prod_env_uses_calibrated_apimart_exchange_rate() -> None:
    values = _env_values(INFRA / ".env.prod.example")

    assert values["ENGINE_APIMART_CREDIT_USD"] == "0.10"
    assert values["ENGINE_USD_CNY_RATE"] == "7.0"


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
    from_lines = [
        line.strip() for line in dockerfile.splitlines() if line.startswith("FROM ")
    ]

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

    next_config = (REPO_ROOT / "frontend" / "next.config.ts").read_text(
        encoding="utf-8"
    )
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
    assert compose["services"]["worker"]["command"].endswith(
        "-Q default,avatar --loglevel=info"
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
    nginx_conf = (INFRA / "nginx" / "conf.d" / "huadingai.conf").read_text(
        encoding="utf-8"
    )

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


def test_backend_uv_lock_uses_official_sources() -> None:
    lock = (REPO_ROOT / "backend" / "uv.lock").read_text(encoding="utf-8")
    pyproject = (REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")

    assert "pypi.tuna.tsinghua.edu.cn" not in lock
    assert 'registry = "https://pypi.org/simple"' in lock
    assert "https://files.pythonhosted.org/packages" in lock
    assert 'index-url = "https://pypi.org/simple"' in pyproject


def test_prod_env_example_and_runbook_have_placeholders_only() -> None:
    env_example = (INFRA / ".env.prod.example").read_text(encoding="utf-8")
    backend_env_example = (REPO_ROOT / "backend" / ".env.example").read_text(
        encoding="utf-8"
    )
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


def test_deploy_script_runs_one_command_deploy_sequence() -> None:
    script = (INFRA / "deploy.sh").read_text(encoding="utf-8")

    assert "git pull --ff-only" in script
    assert 'up -d --build' in script
    assert 'restart nginx' in script
    assert 'exec -T backend alembic current' in script
    assert '${DRY_RUN:-0}' in script
