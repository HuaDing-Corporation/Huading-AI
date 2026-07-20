import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session, get_progress_store
from app.db.models import Base, ProviderConfig, UsageRecord
from app.main import app


def _json_log_lines(output: str) -> list[dict]:
    records = []
    for line in output.splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def test_healthz_alias_returns_plain_liveness_and_request_id() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "Huading API"
    assert body["version"] == "0.1.0"
    assert response.headers["X-Request-ID"]


def test_readyz_alias_reports_database_boolean(monkeypatch) -> None:
    class FakeDb:
        def execute(self, _statement):
            return None

    class FakeRedis:
        def ping(self):
            return True

    app.dependency_overrides[get_db_session] = lambda: FakeDb()
    try:
        from app.api.v1.routes import health as health_route

        app.dependency_overrides[health_route.get_redis_client] = lambda: FakeRedis()
        with TestClient(app) as client:
            response = client.get("/api/v1/readyz")
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(health_route.get_redis_client, None)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": True}


def test_validation_error_envelope_contains_nested_request_id_and_details(auth_context) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/videos",
            json={"topic": "x", "pipeline": "evil"},
            headers=auth_context["headers"],
        )

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["request_id"] == body["request_id"]
    assert body["error"]["details"]


def test_access_log_contains_architecture_fields(capsys) -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/healthz", headers={"X-Request-ID": "req-arch-log"})

    assert response.status_code == 200
    records = _json_log_lines(capsys.readouterr().out)
    access = [record for record in records if record.get("event") == "access"]
    assert access
    record = access[-1]
    assert record["request_id"] == "req-arch-log"
    assert record["method"] == "GET"
    assert record["path"] == "/api/v1/healthz"
    assert record["status"] == 200
    assert "latency_ms" in record
    assert "tenant_id" in record
    assert "user_id" in record


def test_unhandled_exception_logs_json_and_returns_error_envelope(capsys, auth_context) -> None:
    def boom() -> None:
        raise RuntimeError("arch-log-boom")

    app.dependency_overrides[get_progress_store] = boom
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(
                "/api/v1/videos/some-id",
                headers={**auth_context["headers"], "X-Request-ID": "req-arch-error"},
            )
    finally:
        app.dependency_overrides.pop(get_progress_store, None)

    body = response.json()
    assert response.status_code == 500
    assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert body["error"]["request_id"] == "req-arch-error"
    records = _json_log_lines(capsys.readouterr().out)
    errors = [record for record in records if record.get("event") == "unhandled_exception"]
    assert errors
    assert errors[-1]["request_id"] == "req-arch-error"
    assert "arch-log-boom" in errors[-1]["error"]


def test_require_role_alias_exists() -> None:
    from app.api.deps import require_role, require_roles

    assert require_role is require_roles


def test_provider_resolve_prefers_tenant_config_and_falls_back_to_platform(
    monkeypatch,
) -> None:
    from app.providers import base as providers_base
    from app.providers.base import clear_provider_registry, register_provider, resolve

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionTesting = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    class FakeProvider:
        def __init__(self, name: str) -> None:
            self.name = name

    monkeypatch.setattr(providers_base, "_REGISTRY", {})
    clear_provider_registry()
    register_provider("llm", "platform-llm", lambda _config: FakeProvider("platform"))
    register_provider("llm", "tenant-llm", lambda _config: FakeProvider("tenant"))

    try:
        with SessionTesting() as db:
            db.add_all(
                [
                    ProviderConfig(
                        tenant_id=None,
                        capability="llm",
                        provider="platform-llm",
                        config={},
                    ),
                    ProviderConfig(
                        tenant_id="tenant-a",
                        capability="llm",
                        provider="tenant-llm",
                        config={},
                    ),
                ]
            )
            db.commit()

            assert resolve(db, tenant_id="tenant-a", capability="llm").name == "tenant"
            assert resolve(db, tenant_id="tenant-b", capability="llm").name == "platform"
    finally:
        clear_provider_registry()
        Base.metadata.drop_all(engine)


def test_provider_resolve_named_provider_and_voice_clone_default(monkeypatch) -> None:
    from app.providers import base as providers_base
    from app.providers.base import (
        clear_provider_registry,
        register_provider,
        resolve,
        resolve_named_provider,
    )

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionTesting = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    class FakeProvider:
        def __init__(self, name: str) -> None:
            self.name = name

    monkeypatch.setattr(providers_base, "_REGISTRY", {})
    clear_provider_registry()
    register_provider("voice_clone", "doubao-voice-clone", lambda _config: FakeProvider("doubao"))
    register_provider(
        "voice_clone",
        "cosyvoice-voice-clone",
        lambda _config: FakeProvider("cosyvoice"),
    )

    try:
        with SessionTesting() as db:
            db.add_all(
                [
                    ProviderConfig(
                        tenant_id=None,
                        capability="voice_clone",
                        provider="doubao-voice-clone",
                        config={},
                    ),
                    ProviderConfig(
                        tenant_id=None,
                        capability="voice_clone",
                        provider="cosyvoice-voice-clone",
                        config={},
                    ),
                ]
            )
            db.commit()

            assert resolve(db, tenant_id="tenant-a", capability="voice_clone").name == "doubao"
            assert (
                resolve_named_provider(
                    db,
                    tenant_id="tenant-a",
                    capability="voice_clone",
                    provider="cosyvoice-voice-clone",
                ).name
                == "cosyvoice"
            )
    finally:
        clear_provider_registry()
        Base.metadata.drop_all(engine)


@pytest.mark.asyncio
async def test_provider_invoke_records_usage() -> None:
    from app.providers.base import ProviderUsage, invoke

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionTesting = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    async def operation() -> dict[str, str]:
        return {"message": "ok"}

    try:
        with SessionTesting() as db:
            result = await invoke(
                db,
                tenant_id="tenant-a",
                capability="llm",
                provider="fake-llm",
                operation=operation,
                usage=ProviderUsage(unit="call", quantity=1, credits=2, cost_cents=0),
            )

            assert result == {"message": "ok"}
            db.flush()
            records = db.query(UsageRecord).all()
            assert len(records) == 1
            assert records[0].tenant_id == "tenant-a"
            assert records[0].capability == "llm"
            assert records[0].provider == "fake-llm"
            assert records[0].status == "settled"
    finally:
        Base.metadata.drop_all(engine)


@pytest.mark.asyncio
async def test_provider_invoke_records_usage_without_committing() -> None:
    from app.providers.base import ProviderUsage, invoke

    class FakeDb:
        def __init__(self) -> None:
            self.added: list[UsageRecord] = []

        def add(self, value: UsageRecord) -> None:
            self.added.append(value)

        def commit(self) -> None:
            raise AssertionError("provider invoke must not commit caller transaction")

    async def operation() -> dict[str, str]:
        return {"message": "ok"}

    db = FakeDb()
    result = await invoke(
        db,  # type: ignore[arg-type]
        tenant_id="tenant-a",
        capability="llm",
        provider="fake-llm",
        operation=operation,
        usage=ProviderUsage(unit="call", quantity=1, credits=2, cost_cents=0),
    )

    assert result == {"message": "ok"}
    assert len(db.added) == 1
    assert db.added[0].tenant_id == "tenant-a"
