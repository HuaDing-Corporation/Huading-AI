from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.exceptions import AppError
from app.db.models import (
    Asset,
    BillingOperation,
    BrandVoice,
    BrandVoiceOrder,
    BrandVoiceProviderId,
    ProviderConfig,
)


def _seed_brand_voice_order(db, *, brand_voice_id: str = "brand-a") -> None:
    now = datetime.now(UTC)
    db.add_all(
        [
            Asset(
                id="asset-a",
                tenant_id="tenant-a",
                type="audio",
                source="upload",
                storage_key="brand-voices/source.wav",
                status="ready",
            ),
            BillingOperation(
                id="billing-a",
                tenant_id="tenant-a",
                user_id="user-a",
                operation="cosyvoice_brand_voice_create",
                idempotency_key="brand-voice-order-a",
                request_hash="r" * 64,
                quote_hash="q" * 64,
                pricing_snapshot={},
                requested_credits=0,
                settled_credits=0,
                released_credits=0,
                status="completed",
                completion_kind="succeeded",
                completed_at=now,
            ),
            BrandVoice(
                id=brand_voice_id,
                tenant_id="tenant-a",
                owner_user_id="user-a",
                name="Brand A",
                status="ready",
                consent_confirmed=True,
            ),
        ]
    )
    db.flush()
    db.add(
        BrandVoiceOrder(
            id="order-a",
            tenant_id="tenant-a",
            user_id="user-a",
            order_type="create",
            requested_name="Brand A",
            source_audio_asset_id="asset-a",
            source_metadata_snapshot={},
            consent_confirmed_at=now,
            billing_operation_id="billing-a",
            status="awaiting_fulfillment",
        )
    )
    db.flush()


@pytest.fixture(autouse=True)
def empty_doubao_id_env(monkeypatch):
    from app.services import provider_voice_registry

    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        [],
        raising=False,
    )
    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_voice_clone_speaker_ids",
        [],
        raising=False,
    )


@pytest.mark.parametrize(
    ("source", "active", "field", "value"),
    [
        ("provider-config", True, "speaker_ids", [" voice-conflict "]),
        ("provider-config", False, "speaker_ids", ["voice-conflict"]),
        ("provider-config", True, "used_speaker_ids", {"voice-conflict": "owner"}),
        ("provider-config", False, "used_speaker_ids", "voice-conflict"),
    ],
)
def test_registry_rejects_ids_from_every_provider_config_shape(
    db_session,
    source,
    active,
    field,
    value,
) -> None:
    from app.services.provider_voice_registry import claim_customer_provider_voice_id

    _seed_brand_voice_order(db_session)
    db_session.add(
        ProviderConfig(
            tenant_id=None,
            capability="voice_clone",
            provider="doubao-voice-clone",
            config={field: value},
            is_active=active,
        )
    )
    db_session.flush()

    with pytest.raises(AppError) as exc:
        claim_customer_provider_voice_id(
            db_session,
            provider_voice_id="voice-conflict",
            brand_voice_id="brand-a",
            order_id="order-a",
        )

    assert source == "provider-config"
    assert exc.value.code == "PROVIDER_VOICE_ID_CONFLICT"


@pytest.mark.parametrize("deleted", [False, True])
def test_registry_rejects_id_from_every_brand_voice_including_soft_deleted(
    db_session,
    deleted,
) -> None:
    from app.services.provider_voice_registry import claim_customer_provider_voice_id

    _seed_brand_voice_order(db_session)
    db_session.add(
        BrandVoice(
            id="brand-owner",
            tenant_id="tenant-a",
            owner_user_id="user-a",
            name="Owner",
            speaker_id="voice-conflict",
            status="ready",
            consent_confirmed=True,
            deleted_at=datetime.now(UTC) if deleted else None,
        )
    )
    db_session.flush()

    with pytest.raises(AppError) as exc:
        claim_customer_provider_voice_id(
            db_session,
            provider_voice_id="voice-conflict",
            brand_voice_id="brand-a",
            order_id="order-a",
        )

    assert exc.value.code == "PROVIDER_VOICE_ID_CONFLICT"


@pytest.mark.parametrize(
    ("status", "provider"),
    [
        ("active", "doubao-voice-clone"),
        ("retired", "doubao-voice-clone"),
        ("active", "historical-provider-alias"),
    ],
)
def test_registry_rejects_every_active_and_retired_registry_id(
    db_session,
    status,
    provider,
) -> None:
    from app.services.provider_voice_registry import claim_customer_provider_voice_id

    _seed_brand_voice_order(db_session)
    db_session.add(
        BrandVoiceProviderId(
            provider=provider,
            normalized_provider_id="voice-conflict",
            kind="customer",
            brand_voice_id="brand-a" if status == "retired" else None,
            first_order_id="order-a" if status == "retired" else None,
            status=status,
        )
    )
    db_session.flush()

    with pytest.raises(AppError) as exc:
        claim_customer_provider_voice_id(
            db_session,
            provider_voice_id="voice-conflict",
            brand_voice_id="brand-a",
            order_id="order-a",
        )

    assert exc.value.code == "PROVIDER_VOICE_ID_CONFLICT"


@pytest.mark.parametrize("env_field", ["engine_doubao_official_voice_ids", "engine_doubao_voice_clone_speaker_ids"])
def test_registry_rejects_official_and_legacy_environment_ids(
    db_session,
    monkeypatch,
    env_field,
) -> None:
    from app.services import provider_voice_registry

    _seed_brand_voice_order(db_session)
    monkeypatch.setattr(provider_voice_registry.settings, env_field, [" voice-env "])

    with pytest.raises(AppError) as exc:
        provider_voice_registry.claim_customer_provider_voice_id(
            db_session,
            provider_voice_id="voice-env",
            brand_voice_id="brand-a",
            order_id="order-a",
        )

    assert exc.value.code == "PROVIDER_VOICE_ID_CONFLICT"


def test_customer_claim_allows_only_exact_active_same_voice_renewal(db_session) -> None:
    from app.services.provider_voice_registry import (
        claim_customer_provider_voice_id,
        retire_customer_provider_voice_id,
    )

    _seed_brand_voice_order(db_session)
    claimed = claim_customer_provider_voice_id(
        db_session,
        provider_voice_id=" voice-owned ",
        brand_voice_id="brand-a",
        order_id="order-a",
    )
    renewed = claim_customer_provider_voice_id(
        db_session,
        provider_voice_id="voice-owned",
        brand_voice_id="brand-a",
        order_id="order-a",
    )
    assert renewed.id == claimed.id

    db_session.add(
        BrandVoice(
            id="brand-b",
            tenant_id="tenant-a",
            owner_user_id="user-a",
            name="Brand B",
            status="ready",
            consent_confirmed=True,
        )
    )
    db_session.flush()
    with pytest.raises(AppError) as exc:
        claim_customer_provider_voice_id(
            db_session,
            provider_voice_id="voice-owned",
            brand_voice_id="brand-b",
            order_id="order-a",
        )
    assert exc.value.code == "PROVIDER_VOICE_ID_CONFLICT"

    retired = retire_customer_provider_voice_id(
        db_session,
        brand_voice_id="brand-a",
        provider_voice_id="voice-owned",
        retired_at=datetime(2026, 8, 29, tzinfo=UTC),
    )
    assert retired.status == "retired"
    assert retired.updated_at == datetime(2026, 8, 29, tzinfo=UTC)


def test_registry_lock_uses_exact_namespace_and_sorted_unique_ids() -> None:
    from app.services.provider_voice_registry import lock_provider_voice_ids

    class PostgresBind:
        class Dialect:
            name = "postgresql"

        dialect = Dialect()

    class CapturingSession:
        statements: list[tuple[str, dict[str, int]]] = []

        @staticmethod
        def get_bind():
            return PostgresBind()

        @classmethod
        def execute(cls, statement, params) -> None:
            cls.statements.append((str(statement), params))

    lock_provider_voice_ids(
        CapturingSession(),
        provider="doubao-voice-clone",
        provider_voice_ids=[" voice-z ", "voice-a", "voice-z"],
    )

    assert all("pg_advisory_xact_lock" in sql for sql, _ in CapturingSession.statements)
    assert [params["lock_id"] for _, params in CapturingSession.statements] == [
        int.from_bytes(
            hashlib.sha256(b"huading:voice-slot:voice-a").digest()[:8],
            byteorder="big",
            signed=True,
        ),
        int.from_bytes(
            hashlib.sha256(b"huading:voice-slot:voice-z").digest()[:8],
            byteorder="big",
            signed=True,
        ),
    ]


def test_switch_claims_new_and_retires_old_under_one_sorted_double_lock(
    db_session,
    monkeypatch,
) -> None:
    from app.services import provider_voice_registry

    _seed_brand_voice_order(db_session)
    old = provider_voice_registry.claim_customer_provider_voice_id(
        db_session,
        provider_voice_id="voice-z-old",
        brand_voice_id="brand-a",
        order_id="order-a",
    )
    lock_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        provider_voice_registry,
        "lock_provider_voice_ids",
        lambda db, *, provider, provider_voice_ids: lock_calls.append(
            tuple(provider_voice_ids)
        ),
    )

    new = provider_voice_registry.claim_customer_provider_voice_id(
        db_session,
        provider_voice_id="voice-a-new",
        previous_provider_voice_id="voice-z-old",
        brand_voice_id="brand-a",
        order_id="order-a",
    )

    assert lock_calls == [("voice-a-new", "voice-z-old")]
    assert new.status == "active"
    assert old.status == "retired"


def test_register_official_ids_requires_exact_config_and_is_idempotent(
    db_session,
    monkeypatch,
) -> None:
    from app.services import provider_voice_registry

    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        ["official-b", "official-a"],
    )
    rows = provider_voice_registry.register_official_provider_voice_ids(
        db_session,
        provider_voice_ids=[" official-a ", "official-b"],
    )
    repeated = provider_voice_registry.register_official_provider_voice_ids(
        db_session,
        provider_voice_ids=["official-b", "official-a"],
    )

    assert [row.normalized_provider_id for row in rows] == ["official-a", "official-b"]
    assert [row.id for row in repeated] == [row.id for row in rows]

    with pytest.raises(AppError) as exc:
        provider_voice_registry.register_official_provider_voice_ids(
            db_session,
            provider_voice_ids=["official-a"],
        )
    assert exc.value.code == "OFFICIAL_PROVIDER_VOICE_IDS_MISMATCH"


def test_registry_readiness_fails_closed_in_production_for_empty_mismatch_and_unknown(
    db_session,
    monkeypatch,
) -> None:
    from app.services import provider_voice_registry

    monkeypatch.setattr(provider_voice_registry.settings, "environment", "production")
    with pytest.raises(AppError) as empty:
        provider_voice_registry.assert_doubao_registry_ready(db_session)
    assert empty.value.code == "DOUBAO_REGISTRY_NOT_READY"

    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        ["official-a"],
    )
    with pytest.raises(AppError) as mismatch:
        provider_voice_registry.assert_doubao_registry_ready(db_session)
    assert mismatch.value.code == "DOUBAO_REGISTRY_NOT_READY"

    provider_voice_registry.register_official_provider_voice_ids(
        db_session,
        provider_voice_ids=["official-a"],
    )
    db_session.add(
        ProviderConfig(
            tenant_id=None,
            capability="voice_clone",
            provider="doubao-voice-clone",
            config={"speaker_ids": ["unknown-history"]},
            is_active=False,
        )
    )
    db_session.flush()
    with pytest.raises(AppError) as unknown:
        provider_voice_registry.assert_doubao_registry_ready(db_session)
    assert unknown.value.code == "DOUBAO_REGISTRY_NOT_READY"


def test_inventory_reports_all_sources_without_writes(db_session, monkeypatch) -> None:
    from app.services import provider_voice_registry

    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        ["official-a"],
    )
    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_voice_clone_speaker_ids",
        ["legacy-a"],
    )
    db_session.add(
        ProviderConfig(
            capability="voice_clone",
            provider="doubao-voice-clone",
            config={"speaker_ids": ["config-a"], "used_speaker_ids": {"used-a": "x"}},
            is_active=False,
        )
    )
    db_session.flush()

    inventory = provider_voice_registry.provider_voice_inventory(db_session)

    assert inventory.official_configured_ids == ("official-a",)
    assert inventory.legacy_configured_ids == ("legacy-a",)
    assert inventory.provider_config_ids == ("config-a", "used-a")
    assert db_session.scalars(select(BrandVoiceProviderId)).all() == []
