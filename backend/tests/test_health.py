import json
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_db_session, get_redis_client
from app.core.config import settings
from app.db.models import (
    Asset,
    BrandVoice,
    BrandVoiceProviderId,
    CreditRate,
    ProviderConfig,
)
from app.main import app


class _ReadyRedis:
    def ping(self) -> bool:
        return True


class _FailingReadyDependency:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def execute(self, *_args, **_kwargs):
        raise RuntimeError(self.secret)

    def ping(self) -> bool:
        raise RuntimeError(self.secret)


_PRICING_SNAPSHOT_TABLES = {
    "credit_rates",
    "subscriptions",
    "billing_operations",
    "usage_records",
    "credit_refund_grants",
    "provider_configs",
    "provider_voice_registry",
    "brand_voices",
    "brand_voice_orders",
    "source_assets",
}


def _seed_persisted_ready_state(db, monkeypatch) -> dict[str, str]:
    from app.services import provider_voice_registry

    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    secret = "health-provider-config-secret"
    source_url = "https://private.example/health-source.wav?signature=secret"
    official_id = "health-allowed-official-id"
    monkeypatch.setattr(
        settings,
        "engine_doubao_official_voice_ids",
        [official_id],
    )
    db.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32))"))
    db.execute(text("DELETE FROM alembic_version"))
    db.execute(text("INSERT INTO alembic_version (version_num) VALUES ('20260829_0038')"))
    db.add_all(
        [
            Asset(
                id="health-source-asset",
                tenant_id="tenant-a",
                type="audio",
                source="upload",
                storage_key="tenants/tenant-a/private/health-source.wav",
                status="ready",
                metadata_={"source_url": source_url},
            ),
            CreditRate(
                id="health-valid-rate",
                capability="image",
                unit="image",
                credits_per_unit=Decimal("9.0000"),
                is_active=False,
            ),
            ProviderConfig(
                id="health-provider-config",
                capability="chat",
                provider="health-private-provider",
                config={"api_key": secret, "source_url": source_url},
                is_active=False,
            ),
            BrandVoice(
                id="health-cosy-brand-voice",
                tenant_id="tenant-a",
                owner_user_id="user-a",
                name="Health Cosy Voice",
                source_audio_asset_id="health-source-asset",
                provider="cosyvoice-voice-clone",
                speaker_id="health-cosy-private-id",
                status="ready",
                consent_confirmed=True,
                consent_confirmed_at=now,
                activated_at=now,
                expires_at=now + timedelta(days=30),
            ),
        ]
    )
    db.flush()
    provider_voice_registry.register_official_provider_voice_ids(
        db,
        provider_voice_ids=[official_id],
    )
    db.commit()
    return {
        "secret": secret,
        "source_url": source_url,
        "official_id": official_id,
        "private_voice_id": "health-cosy-private-id",
    }


def _health_database_session(db):
    def dependency():
        yield db

    return dependency


@pytest.fixture(autouse=True)
def _clear_production_readiness_cache():
    from app.api.v1.routes import health

    health._reset_pricing_readiness_cache()
    yield
    health._reset_pricing_readiness_cache()


def test_health_check() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_live_health_uses_response_envelope() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "ok"
    assert body["error"] is None
    assert body["request_id"]
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_ready_dependency_failures_do_not_disclose_exception_details(monkeypatch) -> None:
    secret = "redis://private-user:private-password@private-host/0"
    failing = _FailingReadyDependency(secret)
    monkeypatch.setattr(settings, "environment", "local")
    app.dependency_overrides[get_db_session] = lambda: failing
    app.dependency_overrides[get_redis_client] = lambda: failing
    try:
        response = TestClient(app).get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_redis_client, None)

    payload = response.json()["data"]
    assert payload["status"] == "degraded"
    assert payload["components"] == [
        {
            "name": "postgres",
            "status": "error",
            "detail": "database readiness check failed",
        },
        {
            "name": "redis",
            "status": "error",
            "detail": "cache readiness check failed",
        },
    ]
    assert secret not in json.dumps(response.json())


def test_production_ready_uses_shared_validator_and_writes_nothing(
    db_session,
    monkeypatch,
    pricing_closure_snapshot,
) -> None:
    from scripts.ops import pricing_closure_readiness as readiness

    sensitive = _seed_persisted_ready_state(db_session, monkeypatch)
    observed: list[
        tuple[bool, bool, tuple[tuple[str, tuple[str, ...]], ...]]
    ] = []
    production_validator = readiness.pricing_closure_readiness

    def observed_validator(db, *, production_mode: bool):
        report = production_validator(db, production_mode=production_mode)
        observed.append(
            (
                production_mode,
                report.ready,
                tuple((item.code, item.record_ids) for item in report.blockers),
            )
        )
        return report

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(
        readiness,
        "pricing_closure_readiness",
        observed_validator,
    )
    before = pricing_closure_snapshot(db_session)
    assert set(before) == _PRICING_SNAPSHOT_TABLES
    app.dependency_overrides[get_db_session] = _health_database_session(db_session)
    app.dependency_overrides[get_redis_client] = lambda: _ReadyRedis()
    try:
        response = TestClient(app).get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_redis_client, None)

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["status"] == "ok", observed
    pricing_component = next(
        item for item in payload["components"] if item["name"] == "pricing_closure"
    )
    assert pricing_component == {"name": "pricing_closure", "status": "ok", "detail": None}
    assert observed == [(True, True, ())]
    assert pricing_closure_snapshot(db_session) == before
    response_text = json.dumps(response.json())
    for forbidden in sensitive.values():
        assert forbidden not in response_text


def test_production_readiness_cache_singleflights_concurrent_validations(monkeypatch) -> None:
    """Concurrent public probes share one bounded full-validator invocation."""
    from app.api.v1.routes import health
    from scripts.ops import pricing_closure_readiness as readiness

    health._reset_pricing_readiness_cache()
    started = threading.Event()
    release = threading.Event()
    calls: list[object] = []

    class _Report:
        ready = True

    def slow_validator(db, *, production_mode: bool):
        assert production_mode is True
        calls.append(db)
        started.set()
        assert release.wait(timeout=1)
        return _Report()

    monkeypatch.setattr(readiness, "pricing_closure_readiness", slow_validator)
    results: list[bool] = []
    first = threading.Thread(
        target=lambda: results.append(health._cached_pricing_closure_ready(object()))
    )
    second = threading.Thread(
        target=lambda: results.append(health._cached_pricing_closure_ready(object()))
    )
    first.start()
    assert started.wait(timeout=1)
    second.start()
    release.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(calls) == 1
    assert results == [True, True]


def test_production_rate_drift_degrades_without_repair_or_disclosure(
    db_session,
    monkeypatch,
    pricing_closure_snapshot,
) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    sensitive = _seed_persisted_ready_state(db_session, monkeypatch)
    drifted_rate_id = "health-drifted-approved-platform-rate"
    db_session.add(
        CreditRate(
            id=drifted_rate_id,
            tenant_id=None,
            capability="image",
            unit="image",
            credits_per_unit=Decimal("81.0000"),
            is_active=True,
        )
    )
    db_session.commit()
    audit = pricing_closure_readiness(db_session, production_mode=True)
    assert (
        "APPROVED_PLATFORM_RATE_MISMATCH",
        (drifted_rate_id,),
    ) in {(blocker.code, blocker.record_ids) for blocker in audit.blockers}

    monkeypatch.setattr(settings, "environment", "production")
    before = pricing_closure_snapshot(db_session)
    app.dependency_overrides[get_db_session] = _health_database_session(db_session)
    app.dependency_overrides[get_redis_client] = lambda: _ReadyRedis()
    try:
        response = TestClient(app).get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_redis_client, None)

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["status"] == "degraded"
    pricing_component = next(
        item for item in payload["components"] if item["name"] == "pricing_closure"
    )
    assert pricing_component == {
        "name": "pricing_closure",
        "status": "error",
        "detail": "pricing closure readiness gate is not ready",
    }
    assert pricing_closure_snapshot(db_session) == before
    response_text = json.dumps(response.json())
    for forbidden in (*sensitive.values(), drifted_rate_id, "81.0000"):
        assert forbidden not in response_text


def test_production_registry_conflict_degrades_without_repair_or_disclosure(
    db_session,
    monkeypatch,
    pricing_closure_snapshot,
) -> None:
    sensitive = _seed_persisted_ready_state(db_session, monkeypatch)
    db_session.execute(
        text("UPDATE brand_voice_provider_ids SET status = 'retired' WHERE kind = 'official'")
    )
    db_session.commit()

    monkeypatch.setattr(settings, "environment", "production")
    before = pricing_closure_snapshot(db_session)
    assert set(before) == _PRICING_SNAPSHOT_TABLES
    app.dependency_overrides[get_db_session] = _health_database_session(db_session)
    app.dependency_overrides[get_redis_client] = lambda: _ReadyRedis()
    try:
        response = TestClient(app).get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_redis_client, None)

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["status"] == "degraded"
    pricing_component = next(
        item for item in payload["components"] if item["name"] == "pricing_closure"
    )
    assert pricing_component == {
        "name": "pricing_closure",
        "status": "error",
        "detail": "pricing closure readiness gate is not ready",
    }
    assert pricing_closure_snapshot(db_session) == before
    response_text = json.dumps(response.json())
    for forbidden in sensitive.values():
        assert forbidden not in response_text


@pytest.mark.parametrize("status", ["active", "retired"])
def test_production_unconfigured_official_registry_row_degrades_without_disclosure(
    db_session,
    monkeypatch,
    status: str,
) -> None:
    sensitive = _seed_persisted_ready_state(db_session, monkeypatch)
    extra_provider_id = f"health-unconfigured-{status}-id"
    db_session.add(
        BrandVoiceProviderId(
            id=f"health-unconfigured-{status}-row",
            provider="doubao-voice-clone",
            normalized_provider_id=extra_provider_id,
            kind="official",
            status=status,
        )
    )
    db_session.commit()
    monkeypatch.setattr(settings, "environment", "production")
    app.dependency_overrides[get_db_session] = _health_database_session(db_session)
    app.dependency_overrides[get_redis_client] = lambda: _ReadyRedis()
    try:
        response = TestClient(app).get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_redis_client, None)

    payload = response.json()["data"]
    assert payload["status"] == "degraded"
    pricing_component = next(
        item for item in payload["components"] if item["name"] == "pricing_closure"
    )
    assert pricing_component == {
        "name": "pricing_closure",
        "status": "error",
        "detail": "pricing closure readiness gate is not ready",
    }
    response_text = json.dumps(response.json())
    assert extra_provider_id not in response_text
    for forbidden in sensitive.values():
        assert forbidden not in response_text


def test_production_schema_mismatch_degrades_fail_closed(db_session, monkeypatch) -> None:
    _seed_persisted_ready_state(db_session, monkeypatch)
    db_session.execute(text("UPDATE alembic_version SET version_num = '20260829_0037'"))
    db_session.commit()
    monkeypatch.setattr(settings, "environment", "production")
    app.dependency_overrides[get_db_session] = _health_database_session(db_session)
    app.dependency_overrides[get_redis_client] = lambda: _ReadyRedis()
    try:
        response = TestClient(app).get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_redis_client, None)

    payload = response.json()["data"]
    assert payload["status"] == "degraded"
    pricing_component = next(
        item for item in payload["components"] if item["name"] == "pricing_closure"
    )
    assert pricing_component == {
        "name": "pricing_closure",
        "status": "error",
        "detail": "pricing closure readiness gate is not ready",
    }


def test_production_official_config_over_limit_degrades_without_disclosure(
    db_session,
    monkeypatch,
) -> None:
    _seed_persisted_ready_state(db_session, monkeypatch)
    oversized_ids = [f"health-configured-id-{index}" for index in range(65)]
    monkeypatch.setattr(settings, "engine_doubao_official_voice_ids", oversized_ids)
    monkeypatch.setattr(settings, "environment", "production")

    app.dependency_overrides[get_db_session] = _health_database_session(db_session)
    app.dependency_overrides[get_redis_client] = lambda: _ReadyRedis()
    try:
        response = TestClient(app).get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_redis_client, None)

    payload = response.json()["data"]
    assert payload["status"] == "degraded"
    response_text = json.dumps(response.json())
    assert all(provider_id not in response_text for provider_id in oversized_ids)
