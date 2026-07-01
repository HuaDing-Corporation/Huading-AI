from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA = REPO_ROOT / "infra"


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


def test_prod_compose_exposes_only_nginx_and_persists_state() -> None:
    compose = _prod_compose()
    services = compose["services"]

    assert {
        "frontend",
        "backend",
        "worker",
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
        "-Q default,avatar,image --loglevel=info"
    )


def test_prod_nginx_enforces_https_and_supports_api_sse_and_minio() -> None:
    nginx_conf = (INFRA / "nginx" / "conf.d" / "huadingai.conf").read_text(
        encoding="utf-8"
    )

    assert "return 301 https://$host$request_uri;" in nginx_conf
    assert "ssl_certificate /etc/letsencrypt/live/huadingai.cn/fullchain.pem;" in nginx_conf
    assert "proxy_pass http://backend:8000/api/" in nginx_conf
    assert "proxy_pass http://frontend:3000" in nginx_conf
    assert "location ~ ^/api/.*/events$" in nginx_conf
    assert "proxy_buffering off;" in nginx_conf
    assert "proxy_read_timeout 600s;" in nginx_conf
    assert "location /minio/" not in nginx_conf
    minio_block = _nginx_location_block(nginx_conf, "/huading-videos/")
    assert "proxy_set_header Host $host;" in minio_block
    assert "proxy_pass http://minio:9000;" in minio_block
    assert "proxy_pass http://minio:9000/;" not in minio_block


def test_prod_env_example_and_runbook_have_placeholders_only() -> None:
    env_example = (INFRA / ".env.prod.example").read_text(encoding="utf-8")
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
        "ENGINE_DOUBAO_TTS_APPID=",
        "ENGINE_DOUBAO_VOICE_CLONE_APPID=",
        "OPENAI_API_KEY=",
        "DEEPSEEK_API_KEY=",
        "NEXT_PUBLIC_API_BASE_URL=https://huadingai.cn/api",
        "NEXT_PUBLIC_USE_MOCK=0",
    ]
    for key in required_keys:
        assert key in env_example

    assert "sk-" not in env_example
    assert "task-" not in env_example
    assert "https://huadingai.cn/minio" not in env_example
    assert "docker compose -f infra/docker-compose.prod.yml up -d --build" in deploy_doc
    assert "certbot certonly --webroot" in deploy_doc
    assert "https://huadingai.cn/huading-videos/" in deploy_doc
    assert "https://huadingai.cn/minio" not in deploy_doc
