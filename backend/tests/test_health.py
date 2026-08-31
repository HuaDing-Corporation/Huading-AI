import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_db_session, get_redis_client
from app.core.config import settings
from app.db.models import Asset, BrandVoice, CreditRate, ProviderConfig
from app.main import app


class _ReadyRedis:
    def ping(self) -> bool:
        return True


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


def test_production_ready_uses_shared_validator_and_writes_nothing(
    db_session,
    monkeypatch,
    pricing_closure_snapshot,
) -> None:
    from scripts.ops import pricing_closure_readiness as readiness

    sensitive = _seed_persisted_ready_state(db_session, monkeypatch)
    observed: list[tuple[bool, bool, tuple[str, ...]]] = []
    production_validator = readiness.pricing_closure_readiness

    def observed_validator(db, *, production_mode: bool):
        report = production_validator(db, production_mode=production_mode)
        observed.append(
            (production_mode, report.ready, tuple(item.code for item in report.blockers))
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
    assert payload["status"] == "ok"
    pricing_component = next(
        item for item in payload["components"] if item["name"] == "pricing_closure"
    )
    assert pricing_component == {"name": "pricing_closure", "status": "ok", "detail": None}
    assert observed == [(True, True, ())]
    assert pricing_closure_snapshot(db_session) == before
    response_text = json.dumps(response.json())
    for forbidden in sensitive.values():
        assert forbidden not in response_text


def test_production_not_ready_uses_persisted_corruption_without_repair_or_disclosure(
    db_session,
    monkeypatch,
    pricing_closure_snapshot,
) -> None:
    from scripts.ops import pricing_closure_readiness as readiness

    sensitive = _seed_persisted_ready_state(db_session, monkeypatch)
    corrupt_rate_id = "health-corrupt-infinity-rate"
    db_session.execute(text("PRAGMA ignore_check_constraints = ON"))
    db_session.execute(
        text(
            "INSERT INTO credit_rates "
            "(id, capability, unit, credits_per_unit, is_active, effective_at) "
            "VALUES (:id, 'image', 'image', 'Infinity', 0, CURRENT_TIMESTAMP)"
        ),
        {"id": corrupt_rate_id},
    )
    db_session.execute(text("PRAGMA ignore_check_constraints = OFF"))
    db_session.commit()
    observed: list[tuple[bool, bool, tuple[tuple[str, tuple[str, ...]], ...]]] = []
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
    assert payload["status"] == "degraded"
    pricing_component = next(
        item for item in payload["components"] if item["name"] == "pricing_closure"
    )
    assert pricing_component == {
        "name": "pricing_closure",
        "status": "error",
        "detail": "pricing closure readiness gate is not ready",
    }
    assert observed == [
        (
            True,
            False,
            (("INVALID_CREDIT_RATE", (corrupt_rate_id,)),),
        )
    ]
    assert pricing_closure_snapshot(db_session) == before
    response_text = json.dumps(response.json())
    for forbidden in (*sensitive.values(), corrupt_rate_id, "Infinity"):
        assert forbidden not in response_text
