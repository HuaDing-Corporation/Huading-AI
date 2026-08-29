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


def _fulfill_seeded_registry(
    db,
    *,
    provider_voice_id: str = "voice-owned",
) -> BrandVoiceProviderId:
    from app.services.provider_voice_registry import claim_customer_provider_voice_id

    row = claim_customer_provider_voice_id(
        db,
        provider_voice_id=provider_voice_id,
        brand_voice_id="brand-a",
        order_id="order-a",
    )
    voice = db.get(BrandVoice, "brand-a")
    order = db.get(BrandVoiceOrder, "order-a")
    voice.provider = "doubao-voice-clone"
    voice.speaker_id = provider_voice_id
    voice.status = "ready"
    order.status = "fulfilled"
    order.fulfilled_brand_voice_id = voice.id
    order.fulfilled_provider_id = row.id
    order.resolver_user_id = "user-a"
    order.fulfilled_at = datetime.now(UTC)
    db.flush()
    return row


@pytest.mark.parametrize(
    ("defect", "expected_reason"),
    [
        ("cosyvoice", "customer_voice_provider_not_canonical"),
        ("missing_owner", "customer_voice_owner_missing"),
        ("not_ready", "customer_voice_not_ready"),
        ("wrong_tenant", "customer_binding_tenant_mismatch"),
        ("wrong_payer", "customer_binding_user_mismatch"),
        ("awaiting_order", "customer_order_not_fulfilled"),
        ("wrong_fulfilled_voice", "customer_order_voice_mismatch"),
        ("wrong_fulfilled_provider", "customer_order_provider_mismatch"),
        ("speaker_swap", "active_customer_speaker_mismatch"),
    ],
)
def test_inventory_rejects_every_inconsistent_customer_binding(
    db_session,
    defect,
    expected_reason,
) -> None:
    from app.services.provider_voice_registry import provider_voice_inventory

    _seed_brand_voice_order(db_session)
    row = _fulfill_seeded_registry(db_session)
    voice = db_session.get(BrandVoice, "brand-a")
    order = db_session.get(BrandVoiceOrder, "order-a")
    if defect == "cosyvoice":
        voice.provider = "cosyvoice"
    elif defect == "missing_owner":
        voice.owner_user_id = None
    elif defect == "not_ready":
        voice.status = "processing"
    elif defect == "wrong_tenant":
        order.tenant_id = "tenant-other"
    elif defect == "wrong_payer":
        order.user_id = "user-other"
    elif defect == "awaiting_order":
        order.status = "awaiting_fulfillment"
    elif defect == "wrong_fulfilled_voice":
        order.fulfilled_brand_voice_id = "brand-other"
    elif defect == "wrong_fulfilled_provider":
        order.fulfilled_provider_id = "provider-other"
    elif defect == "speaker_swap":
        voice.speaker_id = "voice-other"

    inventory = provider_voice_inventory(db_session)

    assert [(item.row_id, item.reason) for item in inventory.registry_blockers] == [
        (row.id, expected_reason)
    ]
    assert inventory.active_customer_registry_ids == ()


def test_inventory_accepts_valid_customer_binding(db_session) -> None:
    from app.services.provider_voice_registry import provider_voice_inventory

    _seed_brand_voice_order(db_session)
    _fulfill_seeded_registry(db_session)

    inventory = provider_voice_inventory(db_session)

    assert inventory.active_customer_registry_ids == ("voice-owned",)
    assert inventory.registry_blockers == ()


def test_inventory_preserves_valid_provider_swap_history(db_session) -> None:
    from app.services import provider_voice_registry

    _seed_brand_voice_order(db_session)
    old = _fulfill_seeded_registry(db_session, provider_voice_id="voice-old")
    now = datetime.now(UTC)
    db_session.add(
        BillingOperation(
            id="billing-b",
            tenant_id="tenant-a",
            user_id="user-a",
            operation="doubao_brand_voice_order_renew",
            idempotency_key="brand-voice-order-b",
            request_hash="s" * 64,
            quote_hash="t" * 64,
            pricing_snapshot={},
            requested_credits=30_000,
            settled_credits=30_000,
            released_credits=0,
            status="completed",
            completion_kind="succeeded",
            completed_at=now,
        )
    )
    db_session.flush()
    db_session.add(
        BrandVoiceOrder(
            id="order-b",
            tenant_id="tenant-a",
            user_id="user-a",
            order_type="renew",
            requested_name="Brand A renewed",
            source_audio_asset_id="asset-a",
            source_metadata_snapshot={},
            consent_confirmed_at=now,
            existing_brand_voice_id="brand-a",
            billing_operation_id="billing-b",
            status="awaiting_fulfillment",
        )
    )
    db_session.flush()
    new = provider_voice_registry.claim_customer_provider_voice_id(
        db_session,
        provider_voice_id="voice-new",
        previous_provider_voice_id="voice-old",
        brand_voice_id="brand-a",
        order_id="order-b",
    )
    voice = db_session.get(BrandVoice, "brand-a")
    renewal = db_session.get(BrandVoiceOrder, "order-b")
    voice.speaker_id = "voice-new"
    renewal.status = "fulfilled"
    renewal.fulfilled_brand_voice_id = voice.id
    renewal.fulfilled_provider_id = new.id
    renewal.resolver_user_id = "user-a"
    renewal.fulfilled_at = now
    db_session.flush()

    inventory = provider_voice_registry.provider_voice_inventory(db_session)

    assert old.status == "retired"
    assert inventory.active_customer_registry_ids == ("voice-new",)
    assert inventory.retired_customer_registry_ids == ("voice-old",)
    assert inventory.registry_blockers == ()


def test_inventory_rejects_retired_id_that_is_still_the_current_speaker(db_session) -> None:
    from app.services import provider_voice_registry

    _seed_brand_voice_order(db_session)
    row = _fulfill_seeded_registry(db_session)
    row.status = "retired"

    inventory = provider_voice_registry.provider_voice_inventory(db_session)

    assert [(item.row_id, item.reason) for item in inventory.registry_blockers] == [
        (row.id, "retired_customer_still_current")
    ]


def test_inventory_rejects_retired_history_without_current_active_binding(db_session) -> None:
    from app.services import provider_voice_registry

    _seed_brand_voice_order(db_session)
    row = _fulfill_seeded_registry(db_session)
    row.status = "retired"
    db_session.get(BrandVoice, "brand-a").speaker_id = "voice-unregistered"

    inventory = provider_voice_registry.provider_voice_inventory(db_session)

    assert [(item.row_id, item.reason) for item in inventory.registry_blockers] == [
        (row.id, "retired_customer_current_binding_missing")
    ]


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
        ("retired", "historical-provider-alias"),
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


def test_claim_rejects_malformed_normalized_registry_id_without_duplicate_insert(
    db_session,
) -> None:
    from app.services.provider_voice_registry import claim_customer_provider_voice_id

    _seed_brand_voice_order(db_session)
    db_session.add(
        BrandVoiceProviderId(
            provider="doubao-voice-clone",
            normalized_provider_id=" voice-conflict ",
            kind="customer",
            brand_voice_id="brand-a",
            first_order_id="order-a",
            status="active",
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
    assert len(list(db_session.scalars(select(BrandVoiceProviderId)))) == 1


@pytest.mark.parametrize(
    "env_field",
    [
        "engine_doubao_official_voice_ids",
        "engine_doubao_voice_clone_speaker_ids",
    ],
)
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
    claimed = _fulfill_seeded_registry(db_session)
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


@pytest.mark.parametrize(
    ("speaker_id", "provider"),
    [
        (None, "doubao-voice-clone"),
        ("voice-different", "doubao-voice-clone"),
        ("", "doubao-voice-clone"),
        ("   ", "doubao-voice-clone"),
        (" voice-owned ", "doubao-voice-clone"),
        ("voice-owned", "cosyvoice"),
    ],
)
def test_own_id_renewal_requires_exact_canonical_brand_voice_binding(
    db_session,
    speaker_id,
    provider,
) -> None:
    from app.services.provider_voice_registry import claim_customer_provider_voice_id

    _seed_brand_voice_order(db_session)
    claimed = _fulfill_seeded_registry(db_session)
    brand_voice = db_session.get(BrandVoice, "brand-a")
    brand_voice.speaker_id = speaker_id
    brand_voice.provider = provider
    db_session.flush()
    registry_before = (
        claimed.provider,
        claimed.normalized_provider_id,
        claimed.kind,
        claimed.brand_voice_id,
        claimed.first_order_id,
        claimed.status,
    )
    brand_voice_before = (
        brand_voice.provider,
        brand_voice.speaker_id,
        brand_voice.status,
        brand_voice.deleted_at,
    )
    registry_count = len(list(db_session.scalars(select(BrandVoiceProviderId))))
    brand_voice_count = len(list(db_session.scalars(select(BrandVoice))))

    with pytest.raises(AppError) as exc:
        claim_customer_provider_voice_id(
            db_session,
            provider_voice_id="voice-owned",
            brand_voice_id="brand-a",
            order_id="order-a",
        )

    assert exc.value.code == "PROVIDER_VOICE_ID_CONFLICT"
    assert (
        claimed.provider,
        claimed.normalized_provider_id,
        claimed.kind,
        claimed.brand_voice_id,
        claimed.first_order_id,
        claimed.status,
    ) == registry_before
    assert (
        brand_voice.provider,
        brand_voice.speaker_id,
        brand_voice.status,
        brand_voice.deleted_at,
    ) == brand_voice_before
    assert len(list(db_session.scalars(select(BrandVoiceProviderId)))) == registry_count
    assert len(list(db_session.scalars(select(BrandVoice)))) == brand_voice_count


def test_own_id_renewal_rejects_other_sources_without_registry_state_change(
    db_session,
    monkeypatch,
) -> None:
    from app.services import provider_voice_registry

    _seed_brand_voice_order(db_session)
    claimed = _fulfill_seeded_registry(db_session)
    original = (
        claimed.provider,
        claimed.kind,
        claimed.brand_voice_id,
        claimed.first_order_id,
        claimed.status,
    )

    def assert_rejected() -> None:
        with pytest.raises(AppError) as exc:
            provider_voice_registry.claim_customer_provider_voice_id(
                db_session,
                provider_voice_id="voice-owned",
                brand_voice_id="brand-a",
                order_id="order-a",
            )
        assert exc.value.code == "PROVIDER_VOICE_ID_CONFLICT"
        assert (
            claimed.provider,
            claimed.kind,
            claimed.brand_voice_id,
            claimed.first_order_id,
            claimed.status,
        ) == original
        assert len(list(db_session.scalars(select(BrandVoiceProviderId)))) == 1

    other_voice = BrandVoice(
        id="brand-other",
        tenant_id="tenant-a",
        owner_user_id="user-a",
        name="Other",
        speaker_id="voice-owned",
        status="ready",
        consent_confirmed=True,
    )
    db_session.add(other_voice)
    db_session.flush()
    assert_rejected()
    db_session.delete(other_voice)
    db_session.flush()

    config = ProviderConfig(
        capability="chat",
        provider="other-provider",
        config={"used_speaker_ids": {"voice-owned": "historical-owner"}},
        is_active=False,
    )
    db_session.add(config)
    db_session.flush()
    assert_rejected()
    db_session.delete(config)
    db_session.flush()

    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_voice_clone_speaker_ids",
        ["voice-owned"],
    )
    assert_rejected()
    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_voice_clone_speaker_ids",
        [],
    )
    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        ["voice-owned"],
    )
    assert_rejected()
    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        [],
    )

    claimed.provider = "historical-provider-alias"
    db_session.flush()
    alias_original = (
        claimed.provider,
        claimed.kind,
        claimed.brand_voice_id,
        claimed.first_order_id,
        claimed.status,
    )
    with pytest.raises(AppError) as alias:
        provider_voice_registry.claim_customer_provider_voice_id(
            db_session,
            provider_voice_id="voice-owned",
            brand_voice_id="brand-a",
            order_id="order-a",
        )
    assert alias.value.code == "PROVIDER_VOICE_ID_CONFLICT"
    assert (
        claimed.provider,
        claimed.kind,
        claimed.brand_voice_id,
        claimed.first_order_id,
        claimed.status,
    ) == alias_original


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


def test_registry_snapshot_lock_serializes_global_then_ids_rows_and_configs() -> None:
    from app.services.provider_voice_registry import lock_provider_voice_registry_snapshot

    class PostgresBind:
        class Dialect:
            name = "postgresql"

        dialect = Dialect()

    class CapturingSession:
        events: list[tuple[str, object]] = []

        @staticmethod
        def get_bind():
            return PostgresBind()

        @classmethod
        def execute(cls, statement, params) -> None:
            cls.events.append(("advisory", params["lock_id"]))

        @classmethod
        def scalars(cls, statement):
            cls.events.append(("rows", str(statement)))
            return []

    lock_provider_voice_registry_snapshot(
        CapturingSession(),
        provider_voice_ids=["voice-z", "voice-a", "voice-z"],
    )

    assert CapturingSession.events[:3] == [
        (
            "advisory",
            int.from_bytes(
                hashlib.sha256(b"huading:voice-slot:__registry_global__").digest()[:8],
                byteorder="big",
                signed=True,
            ),
        ),
        (
            "advisory",
            int.from_bytes(
                hashlib.sha256(b"huading:voice-slot:voice-a").digest()[:8],
                byteorder="big",
                signed=True,
            ),
        ),
        (
            "advisory",
            int.from_bytes(
                hashlib.sha256(b"huading:voice-slot:voice-z").digest()[:8],
                byteorder="big",
                signed=True,
            ),
        ),
    ]
    assert "brand_voice_provider_ids" in CapturingSession.events[3][1]
    assert "ORDER BY brand_voice_provider_ids.id" in CapturingSession.events[3][1]
    assert "provider_configs" in CapturingSession.events[4][1]
    assert "ORDER BY provider_configs.id" in CapturingSession.events[4][1]


def test_registry_writers_and_readiness_share_the_snapshot_lock(
    db_session,
    monkeypatch,
) -> None:
    from app.services import provider_voice_registry

    snapshot_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        provider_voice_registry,
        "lock_provider_voice_registry_snapshot",
        lambda db, *, provider_voice_ids=(): snapshot_calls.append(
            tuple(provider_voice_ids)
        ),
    )
    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        ["official-a"],
    )
    provider_voice_registry.register_official_provider_voice_ids(
        db_session,
        provider_voice_ids=["official-a"],
    )
    _seed_brand_voice_order(db_session)
    claimed = provider_voice_registry.claim_customer_provider_voice_id(
        db_session,
        provider_voice_id="customer-a",
        brand_voice_id="brand-a",
        order_id="order-a",
    )
    voice = db_session.get(BrandVoice, "brand-a")
    order = db_session.get(BrandVoiceOrder, "order-a")
    voice.provider = "doubao-voice-clone"
    voice.speaker_id = "customer-a"
    order.status = "fulfilled"
    order.fulfilled_brand_voice_id = voice.id
    order.fulfilled_provider_id = claimed.id
    order.resolver_user_id = "user-a"
    order.fulfilled_at = datetime.now(UTC)
    db_session.flush()

    monkeypatch.setattr(provider_voice_registry.settings, "environment", "production")
    provider_voice_registry.assert_doubao_registry_ready(db_session)
    provider_voice_registry.retire_customer_provider_voice_id(
        db_session,
        brand_voice_id=voice.id,
        provider_voice_id="customer-a",
        retired_at=datetime.now(UTC),
    )

    assert snapshot_calls == [
        ("official-a",),
        ("customer-a",),
        (),
        ("customer-a",),
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

    assert lock_calls == [
        ("__registry_global__",),
        ("voice-a-new", "voice-z-old"),
    ]
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


def test_registry_readiness_accepts_exact_config_and_valid_official_rows(
    db_session,
    monkeypatch,
) -> None:
    from app.services import provider_voice_registry

    monkeypatch.setattr(provider_voice_registry.settings, "environment", "production")
    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        ["official-b", "official-a"],
    )
    provider_voice_registry.register_official_provider_voice_ids(
        db_session,
        provider_voice_ids=["official-a", "official-b"],
    )

    provider_voice_registry.assert_doubao_registry_ready(db_session)


def test_inventory_scans_cross_provider_and_cross_capability_configs(
    db_session,
) -> None:
    from app.services.provider_voice_registry import provider_voice_inventory

    db_session.add_all(
        [
            ProviderConfig(
                capability="chat",
                provider="unrelated-active-provider",
                config={"speaker_ids": ["cross-capability-speaker"]},
                is_active=True,
            ),
            ProviderConfig(
                capability="voice_clone",
                provider="unrelated-inactive-provider",
                config={
                    "used_speaker_ids": {
                        "cross-provider-used": "historical-owner"
                    }
                },
                is_active=False,
            ),
        ]
    )
    db_session.flush()

    inventory = provider_voice_inventory(db_session)

    assert inventory.provider_config_ids == (
        "cross-capability-speaker",
        "cross-provider-used",
    )


def test_readiness_blocks_alias_and_malformed_registry_rows(
    db_session,
    monkeypatch,
) -> None:
    from app.services import provider_voice_registry

    _seed_brand_voice_order(db_session)
    monkeypatch.setattr(provider_voice_registry.settings, "environment", "production")
    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        ["official-ok"],
    )
    provider_voice_registry.register_official_provider_voice_ids(
        db_session,
        provider_voice_ids=["official-ok"],
    )
    db_session.add_all(
        [
            BrandVoiceProviderId(
                provider="historical-provider-alias",
                normalized_provider_id="alias-active",
                kind="customer",
                brand_voice_id="brand-a",
                first_order_id="order-a",
                status="active",
            ),
            BrandVoiceProviderId(
                provider="unknown-provider",
                normalized_provider_id="alias-retired",
                kind="customer",
                brand_voice_id="brand-a",
                first_order_id="order-a",
                status="retired",
            ),
            BrandVoiceProviderId(
                provider="doubao-voice-clone",
                normalized_provider_id="official-with-owner",
                kind="official",
                brand_voice_id="brand-a",
                first_order_id="order-a",
                status="active",
            ),
            BrandVoiceProviderId(
                provider="doubao-voice-clone",
                normalized_provider_id="customer-without-owner",
                kind="customer",
                status="active",
            ),
            BrandVoiceProviderId(
                provider="doubao-voice-clone",
                normalized_provider_id="customer-without-order",
                kind="customer",
                brand_voice_id="brand-a",
                status="retired",
            ),
            BrandVoiceProviderId(
                provider="doubao-voice-clone",
                normalized_provider_id="official-retired",
                kind="official",
                status="retired",
            ),
        ]
    )
    db_session.flush()

    inventory = provider_voice_registry.provider_voice_inventory(db_session)

    assert {item.provider_voice_id for item in inventory.registry_blockers} == {
        "alias-active",
        "alias-retired",
        "official-with-owner",
        "customer-without-owner",
        "customer-without-order",
        "official-retired",
    }
    assert "alias-active" not in inventory.active_customer_registry_ids
    assert "alias-retired" not in inventory.retired_customer_registry_ids
    with pytest.raises(AppError) as exc:
        provider_voice_registry.assert_doubao_registry_ready(db_session)
    assert exc.value.code == "DOUBAO_REGISTRY_NOT_READY"


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


def test_inventory_includes_soft_deleted_doubao_history_but_ignores_cosyvoice_ids(
    db_session,
    monkeypatch,
) -> None:
    from app.services.provider_voice_registry import provider_voice_inventory

    monkeypatch.setattr(
        "app.services.provider_voice_registry.settings.engine_doubao_official_voice_ids",
        ["doubao-deleted"],
    )
    db_session.add_all(
        [
            BrandVoice(
                id="soft-deleted-doubao",
                tenant_id="tenant-a",
                owner_user_id="user-a",
                name="Deleted Doubao",
                provider="doubao-voice-clone",
                speaker_id="doubao-deleted",
                status="ready",
                consent_confirmed=True,
                deleted_at=datetime(2026, 8, 29, tzinfo=UTC),
            ),
            BrandVoice(
                id="cosyvoice-inventory",
                tenant_id="tenant-a",
                owner_user_id="user-a",
                name="CosyVoice",
                provider="cosyvoice-voice-clone",
                speaker_id="cosy-private-id",
                status="ready",
                consent_confirmed=True,
            ),
        ]
    )
    db_session.commit()

    inventory = provider_voice_inventory(db_session)

    assert inventory.brand_voice_ids == ("doubao-deleted",)
    assert "cosy-private-id" not in inventory.unknown_ids
