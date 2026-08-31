from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_object_storage
from app.core.exceptions import AppError
from app.core.security import create_access_token
from app.db.models import (
    Asset,
    BillingOperation,
    BrandVoice,
    BrandVoiceOrder,
    BrandVoiceProviderId,
    CreditRate,
    Plan,
    ProviderConfig,
    Subscription,
    Tenant,
    UsageRecord,
    User,
    VideoTask,
    Voice,
)
from app.main import app
from app.workers import avatar_talk


class _CloneProvider:
    def __init__(self, provider_name: str = "doubao-voice-clone") -> None:
        self.provider_name = provider_name
        self.clone_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []

    async def clone_voice(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.clone_calls.append(dict(payload))
        return {
            "speaker_id": str(payload.get("speaker_id") or "brand-speaker-001"),
            "status": "ready",
            "provider": self.provider_name,
        }

    async def delete_voice(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.delete_calls.append(dict(payload))
        return {"released": True}


class _CloneFailureProvider:
    async def clone_voice(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise _HttpCloneError()


class _HttpCloneError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("volcengine voice clone rejected request")
        self.response = _HttpErrorResponse()


class _HttpErrorResponse:
    status_code = 400
    text = '{"code":"InvalidAudio","message":"audio duration too short"}'


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warnings.append((event, kwargs))


class _Storage:
    bucket = "bucket"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        self.objects[key] = content
        return f"memory://{key}"

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        return f"https://storage.test/{key}"

    def delete_object(self, key: str) -> None:
        self.objects.pop(key, None)


class _Store:
    def update(self, *args, **kwargs) -> None:
        return None


def _passthrough_label(content: bytes, **_kwargs) -> bytes:
    return content


def _active_subscription(db, tenant_id: str) -> Subscription:
    subscription = db.scalar(
        select(Subscription)
        .where(Subscription.tenant_id == tenant_id, Subscription.status == "active")
        .order_by(Subscription.period_end.desc())
    )
    assert subscription is not None
    return subscription


def _seed_brand_voice_billing(
    db,
    tenant_id: str,
    *,
    speaker_ids: tuple[str, ...] = ("S_brand_slot_001",),
    huading_access: bool = True,
) -> str:
    subscription = _active_subscription(db, tenant_id)
    if huading_access:
        plan = db.scalar(select(Plan).where(Plan.code == "huading"))
        if plan is None:
            plan = Plan(
                code="huading",
                name="Huading Plan",
                price_cents=0,
                period="monthly",
                quota_credits=0,
                is_active=True,
            )
            db.add(plan)
            db.flush()
        subscription.plan_id = plan.id
    subscription.quota_credits_total = 100000
    subscription.quota_credits_used = 0
    subscription.quota_credits_reserved = 0
    db.add_all(
        [
            CreditRate(
                tenant_id=None,
                capability="voice_clone",
                unit="call",
                credits_per_unit=Decimal("30000.0000"),
            ),
            CreditRate(
                tenant_id=None,
                capability="avatar",
                unit="second",
                credits_per_unit=Decimal("150.0000"),
            ),
            CreditRate(
                tenant_id=None,
                capability="tts",
                unit="character",
                credits_per_unit=Decimal("0.1000"),
            ),
            ProviderConfig(
                tenant_id=None,
                capability="voice_clone",
                provider="doubao-voice-clone",
                config={"speaker_ids": list(speaker_ids)},
                is_active=True,
            ),
        ]
    )
    db.commit()
    return subscription.id


def _seed_audio_asset(db, tenant_id: str) -> str:
    asset = Asset(
        tenant_id=tenant_id,
        type="audio",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/brand-voice.wav",
        mime_type="audio/wav",
        size_bytes=512_000,
        duration_ms=6_000,
        status="ready",
    )
    db.add(asset)
    db.commit()
    return asset.id


def _seed_avatar_asset(db, tenant_id: str) -> str:
    asset = Asset(
        tenant_id=tenant_id,
        type="avatar_image",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/avatar.png",
        mime_type="image/png",
        status="ready",
    )
    db.add(asset)
    db.commit()
    return asset.id


def _same_tenant_user(db, *, tenant_id: str, email: str) -> tuple[User, dict[str, str]]:
    user = User(
        tenant_id=tenant_id,
        email=email,
        password_hash="unused",
        role="creator",
        is_active=True,
        status="active",
    )
    db.add(user)
    db.commit()
    token = create_access_token(user_id=user.id, tenant_id=tenant_id, role=user.role)
    return user, {"Authorization": f"Bearer {token}"}


def test_paid_doubao_voice_is_invisible_to_other_tenant_user(auth_context, auth_db):
    now = datetime.now(UTC)
    with auth_db() as db:
        colleague, colleague_headers = _same_tenant_user(
            db,
            tenant_id=auth_context["tenant_id"],
            email="colleague@example.com",
        )
        voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            owner_user_id=auth_context["user_id"],
            name="Payer only",
            provider="doubao-voice-clone",
            speaker_id="S_payer_only",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=now,
            activated_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=364),
        )
        db.add(voice)
        db.commit()
        voice_id = voice.id
        assert colleague.id != voice.owner_user_id

    client = TestClient(app)
    catalog = client.get("/api/v1/voices", headers=colleague_headers)
    detail = client.get(f"/api/v1/brand-voices/{voice_id}", headers=colleague_headers)

    assert catalog.status_code == 200
    assert voice_id not in {item["id"] for item in catalog.json()["data"]["items"]}
    assert detail.status_code == 404


def test_unknown_rightsless_doubao_is_hidden_while_cosyvoice_remains_tenant_shared(
    auth_context,
    auth_db,
):
    with auth_db() as db:
        _colleague, colleague_headers = _same_tenant_user(
            db,
            tenant_id=auth_context["tenant_id"],
            email="shared-cosy@example.com",
        )
        unknown = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Unknown historic Doubao",
            provider="doubao-voice-clone",
            speaker_id="S_unknown_history",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        cosy = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Shared Cosy",
            provider="cosyvoice-voice-clone",
            speaker_id="cosy-shared",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        db.add_all([unknown, cosy])
        db.commit()
        unknown_id = unknown.id
        cosy_id = cosy.id

    client = TestClient(app)
    catalog = client.get("/api/v1/voices", headers=colleague_headers)
    brand_list = client.get("/api/v1/brand-voices", headers=colleague_headers)

    assert unknown_id not in {item["id"] for item in catalog.json()["data"]["items"]}
    assert unknown_id not in {item["id"] for item in brand_list.json()["data"]["items"]}
    assert (
        client.get(f"/api/v1/brand-voices/{unknown_id}", headers=colleague_headers).status_code
        == 404
    )
    assert cosy_id in {item["id"] for item in catalog.json()["data"]["items"]}
    assert (
        client.get(f"/api/v1/brand-voices/{cosy_id}", headers=colleague_headers).status_code == 200
    )


def test_paid_doubao_owner_can_manage_before_expiry_but_expiry_blocks_new_selection(
    auth_context,
    auth_db,
):
    now = datetime.now(UTC)
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        avatar_id = _seed_avatar_asset(db, auth_context["tenant_id"])
        voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            owner_user_id=auth_context["user_id"],
            name="Paid owner voice",
            provider="doubao-voice-clone",
            speaker_id="S_paid_owner",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=now,
            activated_at=now - timedelta(days=10),
            expires_at=now + timedelta(days=10),
        )
        db.add(voice)
        db.commit()
        voice_id = voice.id

    client = TestClient(app)
    headers = auth_context["headers"]
    assert voice_id in {
        item["id"] for item in client.get("/api/v1/voices", headers=headers).json()["data"]["items"]
    }
    assert client.get(f"/api/v1/brand-voices/{voice_id}", headers=headers).status_code == 200
    renamed = client.patch(
        f"/api/v1/brand-voices/{voice_id}",
        json={"name": "Renamed paid voice"},
        headers=headers,
    )
    assert renamed.status_code == 200

    with auth_db() as db:
        stored = db.get(BrandVoice, voice_id)
        stored.expires_at = now - timedelta(seconds=1)
        db.commit()

    assert voice_id not in {
        item["id"] for item in client.get("/api/v1/voices", headers=headers).json()["data"]["items"]
    }
    assert client.get(f"/api/v1/brand-voices/{voice_id}", headers=headers).status_code == 200
    rejected = client.post(
        "/api/v1/videos/estimate",
        json={
            "topic": "expired voice",
            "script": "must not be accepted",
            "voice_id": voice_id,
            "avatar_asset_id": avatar_id,
            "video_mode": "avatar_talk",
        },
        headers=headers,
    )
    assert rejected.status_code == 404
    assert rejected.json()["error"]["code"] == "VOICE_NOT_FOUND"


@pytest.mark.parametrize(
    ("renewal_status", "expected_delivery_status"),
    [
        ("awaiting_fulfillment", "awaiting_fulfillment"),
        ("rejected", "rejected"),
        ("fulfilled", "active"),
    ],
)
def test_brand_voice_delivery_status_uses_latest_renewal_order(
    renewal_status,
    expected_delivery_status,
    auth_context,
    auth_db,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        audio_id = _seed_audio_asset(db, auth_context["tenant_id"])
        voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            owner_user_id=auth_context["user_id"],
            name="Renewal status voice",
            provider="doubao-voice-clone",
            speaker_id="renewal-status-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=now - timedelta(days=400),
            activated_at=now - timedelta(days=400),
            expires_at=now + timedelta(days=1),
        )
        db.add(voice)
        db.flush()
        provider_id = BrandVoiceProviderId(
            provider="doubao-voice-clone",
            normalized_provider_id=voice.speaker_id,
            kind="customer",
            brand_voice_id=voice.id,
            status="active",
        )
        db.add(provider_id)
        db.flush()

        def operation(name: str) -> BillingOperation:
            item = BillingOperation(
                tenant_id=auth_context["tenant_id"],
                user_id=auth_context["user_id"],
                operation=name,
                idempotency_key=str(uuid4()),
                request_hash="a" * 64,
                quote_hash="b" * 64,
                pricing_snapshot={},
                requested_credits=Decimal("30000"),
            )
            db.add(item)
            db.flush()
            return item

        create_operation = operation("doubao_brand_voice_order_create")
        superseded_renew_operation = operation("doubao_brand_voice_order_renew")
        renew_operation = operation("doubao_brand_voice_order_renew")
        renewal_terminal: dict[str, object] = {}
        if renewal_status == "rejected":
            renewal_terminal = {
                "resolver_user_id": auth_context["user_id"],
                "rejected_at": now,
                "rejection_reason": "renewal rejected",
            }
        elif renewal_status == "fulfilled":
            renewal_terminal = {
                "fulfilled_brand_voice_id": voice.id,
                "fulfilled_provider_id": provider_id.id,
                "resolver_user_id": auth_context["user_id"],
                "fulfilled_at": now,
            }
        superseded_status = "fulfilled" if renewal_status == "rejected" else "rejected"
        superseded_terminal: dict[str, object]
        if superseded_status == "fulfilled":
            superseded_terminal = {
                "fulfilled_brand_voice_id": voice.id,
                "fulfilled_provider_id": provider_id.id,
                "resolver_user_id": auth_context["user_id"],
                "fulfilled_at": now,
            }
        else:
            superseded_terminal = {
                "resolver_user_id": auth_context["user_id"],
                "rejected_at": now,
                "rejection_reason": "superseded renewal",
            }
        db.add_all(
            [
                BrandVoiceOrder(
                    tenant_id=auth_context["tenant_id"],
                    user_id=auth_context["user_id"],
                    order_type="create",
                    requested_name=voice.name,
                    source_audio_asset_id=audio_id,
                    source_metadata_snapshot={},
                    consent_confirmed_at=now - timedelta(days=400),
                    billing_operation_id=create_operation.id,
                    status="fulfilled",
                    fulfilled_brand_voice_id=voice.id,
                    fulfilled_provider_id=provider_id.id,
                    resolver_user_id=auth_context["user_id"],
                    fulfilled_at=now - timedelta(days=400),
                    created_at=now - timedelta(days=400),
                    updated_at=now - timedelta(days=400),
                ),
                BrandVoiceOrder(
                    id="00000000-0000-0000-0000-000000000001",
                    tenant_id=auth_context["tenant_id"],
                    user_id=auth_context["user_id"],
                    order_type="renew",
                    requested_name=voice.name,
                    source_audio_asset_id=audio_id,
                    source_metadata_snapshot={},
                    consent_confirmed_at=now,
                    existing_brand_voice_id=voice.id,
                    billing_operation_id=superseded_renew_operation.id,
                    status=superseded_status,
                    created_at=now,
                    updated_at=now,
                    **superseded_terminal,
                ),
                BrandVoiceOrder(
                    id="ffffffff-ffff-ffff-ffff-ffffffffffff",
                    tenant_id=auth_context["tenant_id"],
                    user_id=auth_context["user_id"],
                    order_type="renew",
                    requested_name=voice.name,
                    source_audio_asset_id=audio_id,
                    source_metadata_snapshot={},
                    consent_confirmed_at=now,
                    existing_brand_voice_id=voice.id,
                    billing_operation_id=renew_operation.id,
                    status=renewal_status,
                    created_at=now,
                    updated_at=now,
                    **renewal_terminal,
                ),
            ]
        )
        db.commit()
        voice_id = voice.id

    response = TestClient(app).get(
        f"/api/v1/brand-voices/{voice_id}",
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    assert response.json()["data"]["order_status"] == renewal_status
    assert response.json()["data"]["delivery_status"] == expected_delivery_status


def test_confirmed_official_doubao_is_platform_only_and_never_backfilled_or_charged(
    auth_context,
    auth_db,
    monkeypatch,
):
    from app.services import plan_access
    from app.services import voices as voice_service

    monkeypatch.setattr(plan_access.settings, "engine_platform_tenant_slugs", {"acme"})
    monkeypatch.setattr(voice_service.settings, "engine_doubao_official_voice_ids", ["S_official"])
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        avatar_id = _seed_avatar_asset(db, auth_context["tenant_id"])
        voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Official",
            provider="doubao-voice-clone",
            speaker_id="S_official",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        db.add(voice)
        db.flush()
        db.add(
            BrandVoiceProviderId(
                provider="doubao-voice-clone",
                normalized_provider_id="S_official",
                kind="official",
                status="active",
            )
        )
        db.commit()
        voice_id = voice.id

    client = TestClient(app)
    catalog = client.get("/api/v1/voices", headers=auth_context["headers"])
    quote = client.post(
        "/api/v1/videos/estimate",
        json={
            "topic": "official voice",
            "script": "official narration",
            "voice_id": voice_id,
            "avatar_asset_id": avatar_id,
            "video_mode": "avatar_talk",
        },
        headers=auth_context["headers"],
    )

    assert catalog.status_code == 200
    assert voice_id in {item["id"] for item in catalog.json()["data"]["items"]}
    assert quote.status_code == 200
    with auth_db() as db:
        stored = db.get(BrandVoice, voice_id)
        assert (stored.owner_user_id, stored.activated_at, stored.expires_at) == (None, None, None)
        assert db.scalar(select(BillingOperation)) is None
        assert db.scalar(select(UsageRecord)) is None
        task = VideoTask(
            id="official-worker-task",
            tenant_id=auth_context["tenant_id"],
            created_by_user_id=auth_context["user_id"],
            created_at=datetime.now(UTC),
            mode="avatar_talk",
            video_mode="avatar_talk",
            status="running",
            brand_voice_id=voice_id,
            params={"brand_voice_id": voice_id},
        )
        db.add(task)
        db.commit()
        assert avatar_talk._tts_voice_for_task(
            db,
            task,
            tenant_id=auth_context["tenant_id"],
        ) == ("S_official", "brand_voice", "doubao-voice-clone")


def test_registered_official_doubao_is_hidden_from_customer_tenant(
    auth_context,
    auth_db,
    monkeypatch,
):
    from app.services import voices as voice_service

    monkeypatch.setattr(voice_service.settings, "engine_doubao_official_voice_ids", ["S_official"])
    with auth_db() as db:
        voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Official ID in customer tenant",
            provider="doubao-voice-clone",
            speaker_id="S_official",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        db.add_all(
            [
                voice,
                BrandVoiceProviderId(
                    provider="doubao-voice-clone",
                    normalized_provider_id="S_official",
                    kind="official",
                    status="active",
                ),
            ]
        )
        db.commit()
        voice_id = voice.id

    client = TestClient(app)
    catalog = client.get("/api/v1/voices", headers=auth_context["headers"])
    assert voice_id not in {item["id"] for item in catalog.json()["data"]["items"]}
    assert (
        client.get(f"/api/v1/brand-voices/{voice_id}", headers=auth_context["headers"]).status_code
        == 404
    )


def test_deleting_delivered_doubao_is_local_only(auth_context, auth_db, monkeypatch):
    from app.api.v1.routes import brand_voices as brand_voice_routes

    provider = _CloneProvider()
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve_named_provider",
        lambda *_args, **_kwargs: provider,
    )
    now = datetime.now(UTC)
    with auth_db() as db:
        subscription_id = _seed_brand_voice_billing(db, auth_context["tenant_id"])
        voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            owner_user_id=auth_context["user_id"],
            name="Delivered",
            provider="doubao-voice-clone",
            speaker_id="S_delivered",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=now,
            activated_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=364),
        )
        db.add(voice)
        db.flush()
        registry = BrandVoiceProviderId(
            provider="doubao-voice-clone",
            normalized_provider_id="S_delivered",
            kind="customer",
            status="active",
            brand_voice_id=voice.id,
        )
        db.add(registry)
        db.commit()
        voice_id = voice.id
        registry_id = registry.id
        subscription = db.get(Subscription, subscription_id)
        wallet_before = (
            subscription.quota_credits_used,
            subscription.quota_credits_reserved,
        )

    response = TestClient(app).delete(
        f"/api/v1/brand-voices/{voice_id}",
        headers=auth_context["headers"],
    )

    assert response.status_code == 204
    assert provider.delete_calls == []
    with auth_db() as db:
        voice = db.get(BrandVoice, voice_id)
        registry = db.get(BrandVoiceProviderId, registry_id)
        subscription = db.get(Subscription, subscription_id)
        assert voice.deleted_at is not None
        assert registry.status == "active"
        assert (
            subscription.quota_credits_used,
            subscription.quota_credits_reserved,
        ) == wallet_before


def test_cosyvoice_create_is_signed_free_committed_and_idempotent(
    auth_context,
    auth_db,
    monkeypatch,
):
    from app.api.v1.routes import brand_voices as brand_voice_routes

    class _CommittedCosyProvider(_CloneProvider):
        def __init__(self) -> None:
            super().__init__("cosyvoice-voice-clone")

        async def clone_voice(self, payload: dict[str, Any]) -> dict[str, Any]:
            with auth_db() as db:
                operation = db.scalar(
                    select(BillingOperation).where(
                        BillingOperation.operation == "cosyvoice_brand_voice_create"
                    )
                )
                usage = db.scalar(
                    select(UsageRecord).where(UsageRecord.billing_operation_id == operation.id)
                )
                assert operation.status == "in_progress"
                assert usage.status == "reserved"
                assert usage.credits == 0
            self.clone_calls.append(dict(payload))
            return {
                "speaker_id": "cosy-created",
                "status": "ready",
                "provider": "cosyvoice-voice-clone",
                "model": "cosyvoice-v3-plus",
                "cost_cents": 7,
            }

    provider = _CommittedCosyProvider()
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve_named_provider",
        lambda *_args, **_kwargs: provider,
    )
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        db.add(
            CreditRate(
                tenant_id=auth_context["tenant_id"],
                capability="tts",
                unit="character",
                credits_per_unit=Decimal("0.2000"),
            )
        )
        db.commit()
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])
    storage = _Storage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    payload = {
        "name": "Free Cosy",
        "source_audio_asset_id": asset_id,
        "consent_confirmed": True,
        "provider": "cosyvoice",
    }
    idempotency_key = str(uuid4())
    try:
        client = TestClient(app)
        estimate = client.post(
            "/api/v1/brand-voices/estimate",
            json=payload,
            headers=auth_context["headers"],
        )
        assert estimate.status_code == 200
        quote = estimate.json()["data"]
        assert quote["operation"] == "cosyvoice_brand_voice_create"
        assert quote["payable_credits"] == 0
        assert quote["rate_source"] == "fixed_policy"
        assert quote["disclosures"][0]["unit"] == "character"
        assert quote["disclosures"][0]["reference_unit_credits"] == "0.2000"
        assert "0.2000 积分/字" in quote["disclosures"][0]["rendered_text"]

        headers = {
            **auth_context["headers"],
            "Idempotency-Key": idempotency_key,
            "X-Huading-Quote": quote["quote_token"],
        }
        created = client.post("/api/v1/brand-voices", json=payload, headers=headers)
        replay = client.post("/api/v1/brand-voices", json=payload, headers=headers)
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert created.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["data"]["id"] == created.json()["data"]["id"]
    billing = created.json()["data"]["billing"]
    assert billing["status"] == "settled"
    assert billing["requested_credits"] == 0
    assert billing["released_credits"] == 0
    assert len(provider.clone_calls) == 1
    with auth_db() as db:
        usage = db.scalar(select(UsageRecord))
        assert usage.status == "settled"
        assert usage.credits == 0
        assert usage.cost_cents == 7
        assert usage.provider_usage == {"cost_cents": 7}
        operation = db.scalar(select(BillingOperation))
        assert provider.clone_calls[0]["billing_operation_id"] == operation.id
        assert Decimal(
            str(operation.pricing_snapshot["disclosures"][0]["reference_unit_credits"])
        ) == Decimal("0.2000")


def test_cosyvoice_new_http_key_reuses_stable_remote_request_identity(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    from app.api.v1.routes import brand_voices as brand_voice_routes

    class _RemoteIdempotentProvider:
        def __init__(self) -> None:
            self.clone_calls: list[dict[str, Any]] = []
            self.remote_voices: dict[str, str] = {}
            self.remote_create_calls = 0

        async def clone_voice(self, payload: dict[str, Any]) -> dict[str, Any]:
            self.clone_calls.append(dict(payload))
            external_request_key = payload["external_request_key"]
            speaker_id = self.remote_voices.get(external_request_key)
            if speaker_id is None:
                self.remote_create_calls += 1
                speaker_id = f"remote-{external_request_key[:12]}"
                self.remote_voices[external_request_key] = speaker_id
            return {
                "speaker_id": speaker_id,
                "status": "ready",
                "provider": "cosyvoice-voice-clone",
            }

    provider = _RemoteIdempotentProvider()
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve_named_provider",
        lambda *_args, **_kwargs: provider,
    )
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])
    payload = {
        "name": "Stable remote identity",
        "source_audio_asset_id": asset_id,
        "consent_confirmed": True,
        "provider": "cosyvoice",
    }
    app.dependency_overrides[get_object_storage] = lambda: _Storage()
    try:
        client = TestClient(app)
        quote = client.post(
            "/api/v1/brand-voices/estimate",
            json=payload,
            headers=auth_context["headers"],
        ).json()["data"]
        first = client.post(
            "/api/v1/brand-voices",
            json=payload,
            headers={
                **auth_context["headers"],
                "Idempotency-Key": str(uuid4()),
                "X-Huading-Quote": quote["quote_token"],
            },
        )
        real_request_replay = brand_voice_routes._cosyvoice_request_replay
        initial_lookup_missed = False

        def miss_one_request_replay(*args, **kwargs):
            nonlocal initial_lookup_missed
            if not initial_lookup_missed:
                initial_lookup_missed = True
                return None
            return real_request_replay(*args, **kwargs)

        monkeypatch.setattr(
            brand_voice_routes,
            "_cosyvoice_request_replay",
            miss_one_request_replay,
        )
        second = client.post(
            "/api/v1/brand-voices",
            json=payload,
            headers={
                **auth_context["headers"],
                "Idempotency-Key": str(uuid4()),
                "X-Huading-Quote": quote["quote_token"],
            },
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert len(provider.clone_calls) == 1
    assert provider.remote_create_calls == 1
    assert first.json()["data"] == second.json()["data"]
    with auth_db() as db:
        assert db.query(BillingOperation).count() == 1
        assert db.query(BrandVoice).count() == 1


@pytest.mark.parametrize(
    (
        "add_ambiguous_match",
        "force_reservation_race",
        "expected_status",
        "expected_code",
    ),
    [
        pytest.param(False, False, 201, None, id="unique-top-level-replay"),
        pytest.param(
            True,
            False,
            502,
            "VOICE_CLONE_RECOVERY_AMBIGUOUS",
            id="ambiguous-top-level-replay",
        ),
        pytest.param(False, True, 201, None, id="unique-reservation-race-replay"),
    ],
)
def test_cosyvoice_replay_reconciles_remote_commit_without_duplicate_post(
    auth_context,
    auth_db,
    monkeypatch,
    add_ambiguous_match: bool,
    force_reservation_race: bool,
    expected_status: int,
    expected_code: str | None,
) -> None:
    from app.api.v1.routes import brand_voices as brand_voice_routes
    from app.db import session as db_session_module
    from app.providers.voice_clone import cosyvoice as cosyvoice_module
    from app.providers.voice_clone.cosyvoice import CosyVoiceCloneProvider

    class _ProcessDeath(BaseException):
        pass

    class _Enrollment:
        def __init__(self) -> None:
            self.voices: list[dict[str, str]] = []
            self.create_calls = 0
            self.list_calls = 0

        def list_voices(self, prefix=None, page_index=0, page_size=100):
            self.list_calls += 1
            return [item.copy() for item in self.voices if item["prefix"] == prefix]

        def create_voice(self, target_model: str, prefix: str, url: str) -> str:
            self.create_calls += 1
            voice_id = f"{prefix}-remote-{self.create_calls}"
            self.voices.append({"voice_id": voice_id, "prefix": prefix})
            return voice_id

    class _CrashAfterRemoteCommit(CosyVoiceCloneProvider):
        async def clone_voice(self, payload):
            await super().clone_voice(payload)
            raise _ProcessDeath()

    enrollment = _Enrollment()
    provider_holder = [
        _CrashAfterRemoteCommit(
            api_key="dashscope-key",
            enrollment_service=enrollment,
        )
    ]
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve_named_provider",
        lambda *_args, **_kwargs: provider_holder[0],
    )
    monkeypatch.setattr(db_session_module, "SessionLocal", auth_db)
    monkeypatch.setattr(
        cosyvoice_module,
        "_dashscope_runtime",
        lambda *_args: nullcontext(),
    )
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])
    payload = {
        "name": "Crash recovery voice",
        "source_audio_asset_id": asset_id,
        "consent_confirmed": True,
        "provider": "cosyvoice",
    }
    app.dependency_overrides[get_object_storage] = lambda: _Storage()
    try:
        client = TestClient(app)
        crash_client = TestClient(app, raise_server_exceptions=False)
        quote = client.post(
            "/api/v1/brand-voices/estimate",
            json=payload,
            headers=auth_context["headers"],
        ).json()["data"]
        headers = {
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote["quote_token"],
        }
        crashed = crash_client.post("/api/v1/brand-voices", json=payload, headers=headers)
        assert crashed.status_code == 500
        assert enrollment.create_calls == 1
        if add_ambiguous_match:
            marker = enrollment.voices[0]["prefix"]
            enrollment.voices.append(
                {"voice_id": f"{marker}-ambiguous", "prefix": marker}
            )
        provider_holder[0] = CosyVoiceCloneProvider(
            api_key="dashscope-key",
            enrollment_service=enrollment,
        )
        if force_reservation_race:
            real_find_replay = brand_voice_routes.find_replay
            real_request_replay = brand_voice_routes._cosyvoice_request_replay
            top_level_lookup_missed = False
            request_lookup_missed = False

            def miss_top_level_lookup_once(*args, **kwargs):
                nonlocal top_level_lookup_missed
                if not top_level_lookup_missed:
                    top_level_lookup_missed = True
                    return None
                return real_find_replay(*args, **kwargs)

            def miss_request_lookup_once(*args, **kwargs):
                nonlocal request_lookup_missed
                if not request_lookup_missed:
                    request_lookup_missed = True
                    return None
                return real_request_replay(*args, **kwargs)

            monkeypatch.setattr(
                brand_voice_routes,
                "find_replay",
                miss_top_level_lookup_once,
            )
            monkeypatch.setattr(
                brand_voice_routes,
                "_cosyvoice_request_replay",
                miss_request_lookup_once,
            )

        replay = client.post("/api/v1/brand-voices", json=payload, headers=headers)
        inventory_calls_after_reconciliation = enrollment.list_calls
        terminal_replay = client.post(
            "/api/v1/brand-voices",
            json=payload,
            headers=headers,
        )
        from app.services.cosyvoice_recovery import reconcile_cosyvoice_operation

        with auth_db() as reconciliation_db:
            terminal_outcome = reconcile_cosyvoice_operation(
                reconciliation_db,
                operation_id=reconciliation_db.scalar(
                    select(BillingOperation.id).where(
                        BillingOperation.operation == "cosyvoice_brand_voice_create"
                    )
                ),
                provider=provider_holder[0],
                now=datetime.now(UTC),
                marker_for_request=cosyvoice_module._request_recovery_marker,
            )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert replay.status_code == expected_status, replay.text
    assert terminal_replay.status_code == expected_status, terminal_replay.text
    assert enrollment.create_calls == 1
    assert enrollment.list_calls == inventory_calls_after_reconciliation
    assert terminal_outcome.state == ("succeeded" if expected_code is None else "failed")
    with auth_db() as db:
        operation = db.scalar(select(BillingOperation))
        usage = db.scalar(select(UsageRecord))
        voice = db.scalar(select(BrandVoice))
        if expected_code is None:
            assert operation.completion_kind == "succeeded"
            assert usage.status == "settled"
            assert voice.status == "ready"
            assert voice.speaker_id == enrollment.voices[0]["voice_id"]
            assert replay.json()["data"]["id"] == voice.id
        else:
            assert replay.json()["error"]["code"] == expected_code
            assert operation.completion_kind == "failed"
            assert usage.status == "released"
            assert voice.status == "failed"


def test_cosyvoice_missing_remote_evidence_holds_then_periodic_recovery_expires(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    from app.api.v1.routes import brand_voices as brand_voice_routes
    from app.core.config import settings
    from app.db import session as db_session_module
    from app.providers.voice_clone import cosyvoice as cosyvoice_module
    from app.providers.voice_clone.cosyvoice import CosyVoiceCloneProvider
    from app.services import task_recovery

    class _ProcessDeath(BaseException):
        pass

    class _DieBeforeRemotePost:
        async def clone_voice(self, _payload):
            raise _ProcessDeath()

    class _EmptyEnrollment:
        def __init__(self) -> None:
            self.create_calls = 0
            self.list_calls = 0

        def list_voices(self, prefix=None, page_index=0, page_size=100):
            self.list_calls += 1
            return []

        def create_voice(self, target_model: str, prefix: str, url: str) -> str:
            self.create_calls += 1
            raise AssertionError("reconciliation must never repeat the supplier POST")

    enrollment = _EmptyEnrollment()
    provider_holder = [_DieBeforeRemotePost()]
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve_named_provider",
        lambda *_args, **_kwargs: provider_holder[0],
    )
    monkeypatch.setattr(
        task_recovery,
        "resolve_named_provider",
        lambda *_args, **_kwargs: provider_holder[0],
        raising=False,
    )
    monkeypatch.setattr(db_session_module, "SessionLocal", auth_db)
    monkeypatch.setattr(
        cosyvoice_module,
        "_dashscope_runtime",
        lambda *_args: nullcontext(),
    )
    monkeypatch.setattr(settings, "engine_orphan_task_stale_seconds", 3_600)
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])
    payload = {
        "name": "Missing recovery evidence",
        "source_audio_asset_id": asset_id,
        "consent_confirmed": True,
        "provider": "cosyvoice",
    }
    app.dependency_overrides[get_object_storage] = lambda: _Storage()
    try:
        client = TestClient(app)
        crash_client = TestClient(app, raise_server_exceptions=False)
        quote = client.post(
            "/api/v1/brand-voices/estimate",
            json=payload,
            headers=auth_context["headers"],
        ).json()["data"]
        headers = {
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote["quote_token"],
        }
        crashed = crash_client.post("/api/v1/brand-voices", json=payload, headers=headers)
        assert crashed.status_code == 500
        provider_holder[0] = CosyVoiceCloneProvider(
            api_key="dashscope-key",
            enrollment_service=enrollment,
        )

        held = client.post("/api/v1/brand-voices", json=payload, headers=headers)
        assert held.status_code == 409, held.text
        assert held.json()["error"]["code"] == "BILLING_OPERATION_IN_PROGRESS"
        assert enrollment.list_calls == 1
        monkeypatch.setattr(settings, "engine_orphan_task_stale_seconds", 0)
        with auth_db() as recovery_db:
            summary = task_recovery.recover_stale_billing_operations(
                recovery_db,
                now=datetime.now(UTC) + timedelta(seconds=1),
            )
        inventory_calls_after_recovery = enrollment.list_calls
        terminal_replay = client.post(
            "/api/v1/brand-voices",
            json=payload,
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert summary.released_operation_ids
    assert terminal_replay.status_code == 504, terminal_replay.text
    assert terminal_replay.json()["error"]["code"] == "VOICE_CLONE_RECOVERY_EXPIRED"
    assert enrollment.create_calls == 0
    assert enrollment.list_calls == inventory_calls_after_recovery
    with auth_db() as db:
        operation = db.scalar(select(BillingOperation))
        usage = db.scalar(select(UsageRecord))
        voice = db.scalar(select(BrandVoice))
        assert operation.completion_kind == "failed"
        assert usage.status == "released"
        assert voice.status == "failed"


def test_cosyvoice_route_fails_closed_before_provider_on_durable_true_marker_collision(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    from app.api.v1.routes import brand_voices as brand_voice_routes
    from app.db import session as db_session_module
    from app.providers.voice_clone import cosyvoice as cosyvoice_module
    from app.providers.voice_clone.cosyvoice import CosyVoiceCloneProvider

    class _Enrollment:
        def __init__(self) -> None:
            self.voices: list[dict[str, str]] = []
            self.create_calls: list[dict[str, str]] = []

        def list_voices(self, prefix=None, page_index=0, page_size=10):
            return [item.copy() for item in self.voices if item["prefix"] == prefix]

        def create_voice(self, target_model: str, prefix: str, url: str) -> str:
            self.create_calls.append(
                {"target_model": target_model, "prefix": prefix, "url": url}
            )
            voice_id = f"{prefix}-speaker-{len(self.create_calls)}"
            self.voices.append({"voice_id": voice_id, "prefix": prefix})
            return voice_id

    enrollment = _Enrollment()
    provider = CosyVoiceCloneProvider(
        api_key="dashscope-key",
        enrollment_service=enrollment,
    )
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve_named_provider",
        lambda *_args, **_kwargs: provider,
    )
    monkeypatch.setattr(db_session_module, "SessionLocal", auth_db)
    monkeypatch.setattr(
        cosyvoice_module,
        "_request_recovery_marker",
        lambda _request_key: "collision",
    )
    monkeypatch.setattr(
        cosyvoice_module,
        "_dashscope_runtime",
        lambda *_args: nullcontext(),
    )
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])
    storage = _Storage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    payloads = (
        {
            "name": "Marker owner A",
            "source_audio_asset_id": asset_id,
            "consent_confirmed": True,
            "provider": "cosyvoice",
        },
        {
            "name": "Marker collider B",
            "source_audio_asset_id": asset_id,
            "consent_confirmed": True,
            "provider": "cosyvoice",
        },
    )
    responses = []
    try:
        client = TestClient(app)
        for payload in payloads:
            quote = client.post(
                "/api/v1/brand-voices/estimate",
                json=payload,
                headers=auth_context["headers"],
            ).json()["data"]
            responses.append(
                client.post(
                    "/api/v1/brand-voices",
                    json=payload,
                    headers={
                        **auth_context["headers"],
                        "Idempotency-Key": str(uuid4()),
                        "X-Huading-Quote": quote["quote_token"],
                    },
                )
            )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert responses[0].status_code == 201, responses[0].text
    assert responses[1].status_code == 502, responses[1].text
    assert responses[1].json()["error"]["code"] == "VOICE_CLONE_FAILED"
    assert len(enrollment.create_calls) == 1
    with auth_db() as db:
        operations = list(
            db.scalars(
                select(BillingOperation).order_by(
                    BillingOperation.created_at,
                    BillingOperation.id,
                )
            )
        )
        assert len(operations) == 2
        assert operations[0].completion_kind == "succeeded"
        assert operations[1].completion_kind == "failed"
        assert operations[0].request_hash != operations[1].request_hash


def test_cosyvoice_delete_loses_to_in_progress_finalize_without_orphan(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    from app.api.v1.routes import brand_voices as brand_voice_routes

    delete_responses = []

    class _DeleteDuringCloneProvider:
        async def clone_voice(self, payload):
            delete_responses.append(
                TestClient(app).delete(
                    f"/api/v1/brand-voices/{payload['brand_voice_id']}",
                    headers=auth_context["headers"],
                )
            )
            return {
                "speaker_id": "cosy-race-ready",
                "status": "ready",
                "provider": "cosyvoice-voice-clone",
            }

    monkeypatch.setattr(
        brand_voice_routes,
        "resolve_named_provider",
        lambda *_args, **_kwargs: _DeleteDuringCloneProvider(),
    )
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])
    payload = {
        "name": "Finalize wins race",
        "source_audio_asset_id": asset_id,
        "consent_confirmed": True,
        "provider": "cosyvoice",
    }
    storage = _Storage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        quote = client.post(
            "/api/v1/brand-voices/estimate",
            json=payload,
            headers=auth_context["headers"],
        ).json()["data"]
        created = client.post(
            "/api/v1/brand-voices",
            json=payload,
            headers={
                **auth_context["headers"],
                "Idempotency-Key": str(uuid4()),
                "X-Huading-Quote": quote["quote_token"],
            },
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert delete_responses[0].status_code == 409
    assert delete_responses[0].json()["error"]["code"] == "BRAND_VOICE_CREATION_IN_PROGRESS"
    assert created.status_code == 201
    with auth_db() as db:
        operation = db.scalar(select(BillingOperation))
        voice = db.get(BrandVoice, created.json()["data"]["id"])
        assert operation.completion_kind == "succeeded"
        assert voice.status == "ready"
        assert voice.deleted_at is None


def test_cosyvoice_delete_after_finalize_releases_remote_once(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    from app.api.v1.routes import brand_voices as brand_voice_routes

    provider = _CloneProvider("cosyvoice-voice-clone")
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve_named_provider",
        lambda *_args, **_kwargs: provider,
    )
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])
    payload = {
        "name": "Finalize then delete",
        "source_audio_asset_id": asset_id,
        "consent_confirmed": True,
        "provider": "cosyvoice",
    }
    app.dependency_overrides[get_object_storage] = lambda: _Storage()
    try:
        client = TestClient(app)
        quote = client.post(
            "/api/v1/brand-voices/estimate",
            json=payload,
            headers=auth_context["headers"],
        ).json()["data"]
        created = client.post(
            "/api/v1/brand-voices",
            json=payload,
            headers={
                **auth_context["headers"],
                "Idempotency-Key": str(uuid4()),
                "X-Huading-Quote": quote["quote_token"],
            },
        )
        deleted = client.delete(
            f"/api/v1/brand-voices/{created.json()['data']['id']}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert created.status_code == 201, created.text
    assert deleted.status_code == 204, deleted.text
    assert len(provider.delete_calls) == 1
    with auth_db() as db:
        operation = db.scalar(select(BillingOperation))
        voice = db.get(BrandVoice, created.json()["data"]["id"])
        assert operation.completion_kind == "succeeded"
        assert voice.deleted_at is not None
        assert provider.delete_calls[0]["speaker_id"] == voice.speaker_id


def test_cosyvoice_create_failure_releases_zero_and_replays_without_remote_call(
    auth_context,
    auth_db,
    monkeypatch,
):
    from app.api.v1.routes import brand_voices as brand_voice_routes

    class _FailingCosyProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def clone_voice(self, _payload):
            self.calls += 1
            raise RuntimeError("supplier rejected clone")

    provider = _FailingCosyProvider()
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve_named_provider",
        lambda *_args, **_kwargs: provider,
    )
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])
    payload = {
        "name": "Failing Cosy",
        "source_audio_asset_id": asset_id,
        "consent_confirmed": True,
        "provider": "cosyvoice",
    }
    client = TestClient(app)
    quote = client.post(
        "/api/v1/brand-voices/estimate",
        json=payload,
        headers=auth_context["headers"],
    ).json()["data"]
    headers = {
        **auth_context["headers"],
        "Idempotency-Key": str(uuid4()),
        "X-Huading-Quote": quote["quote_token"],
    }

    failed = client.post("/api/v1/brand-voices", json=payload, headers=headers)
    replay = client.post("/api/v1/brand-voices", json=payload, headers=headers)
    explicit_retry = client.post(
        "/api/v1/brand-voices",
        json=payload,
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote["quote_token"],
        },
    )

    assert failed.status_code == 502
    assert replay.status_code == 502
    assert explicit_retry.status_code == 502
    assert provider.calls == 2
    with auth_db() as db:
        operations = list(db.scalars(select(BillingOperation).order_by(BillingOperation.id)))
        usages = list(db.scalars(select(UsageRecord).order_by(UsageRecord.id)))
        assert len(operations) == 2
        assert len(usages) == 2
        assert all(operation.status == "completed" for operation in operations)
        assert all(operation.completion_kind == "failed" for operation in operations)
        assert all(
            (operation.requested_credits, operation.released_credits) == (0, 0)
            for operation in operations
        )
        assert all(usage.status == "released" for usage in usages)
        assert all(usage.credits == 0 for usage in usages)


@pytest.mark.parametrize(
    ("invalid_result", "expected_cost_cents"),
    [
        ({"speaker_id": "missing-provider", "status": "ready", "cost_cents": 9}, 9),
        (
            {
                "speaker_id": "wrong-provider",
                "status": "ready",
                "provider": "doubao-voice-clone",
            },
            0,
        ),
        (
            {"speaker_id": "missing-status", "provider": "cosyvoice-voice-clone"},
            0,
        ),
        ({"status": "ready", "provider": "cosyvoice-voice-clone"}, 0),
        (
            {"speaker_id": 123, "status": "ready", "provider": "cosyvoice-voice-clone"},
            0,
        ),
        (
            {
                "speaker_id": "wrong-status",
                "status": "READY",
                "provider": "cosyvoice-voice-clone",
            },
            0,
        ),
        (
            {
                "speaker_id": "extra-field",
                "status": "ready",
                "provider": "cosyvoice-voice-clone",
                "unexpected": True,
            },
            0,
        ),
        ({"speaker_id": "wrong-provider-type", "status": "ready", "provider": 123}, 0),
    ],
)
def test_cosyvoice_create_rejects_invalid_typed_provider_result(
    invalid_result,
    expected_cost_cents,
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    from app.api.v1.routes import brand_voices as brand_voice_routes

    class _MissingProviderResult:
        async def clone_voice(self, _payload):
            return invalid_result

    monkeypatch.setattr(
        brand_voice_routes,
        "resolve_named_provider",
        lambda *_args, **_kwargs: _MissingProviderResult(),
    )
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])
    payload = {
        "name": "Strict provider result",
        "source_audio_asset_id": asset_id,
        "consent_confirmed": True,
        "provider": "cosyvoice",
    }
    storage = _Storage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        quote = client.post(
            "/api/v1/brand-voices/estimate",
            json=payload,
            headers=auth_context["headers"],
        ).json()["data"]
        response = client.post(
            "/api/v1/brand-voices",
            json=payload,
            headers={
                **auth_context["headers"],
                "Idempotency-Key": str(uuid4()),
                "X-Huading-Quote": quote["quote_token"],
            },
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "VOICE_CLONE_FAILED"
    with auth_db() as db:
        operation = db.scalar(select(BillingOperation))
        usage = db.scalar(select(UsageRecord))
        voice = db.scalar(select(BrandVoice))
        assert operation.completion_kind == "failed"
        assert operation.released_credits == 0
        assert usage.status == "released"
        assert usage.cost_cents == expected_cost_cents
        assert usage.provider_usage == {"cost_cents": expected_cost_cents}
        assert voice.status == "failed"


@pytest.mark.parametrize("provider", [None, "doubao", "doubao-voice-clone"])
@pytest.mark.parametrize(
    "billing_headers",
    [
        {},
        {"Idempotency-Key": "33333333-3333-4333-8333-333333333333"},
        {"X-Huading-Quote": "partial-quote"},
        {"Idempotency-Key": "malformed", "X-Huading-Quote": "malformed-quote"},
    ],
)
def test_legacy_doubao_create_requires_manual_order_without_side_effects(
    auth_context,
    auth_db,
    monkeypatch,
    provider,
    billing_headers,
):
    from app.api.v1.routes import brand_voices as brand_voice_routes

    monkeypatch.setattr(
        brand_voice_routes,
        "resolve_named_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("manual Doubao path must not resolve a provider")
        ),
    )
    with auth_db() as db:
        subscription_id = _seed_brand_voice_billing(db, auth_context["tenant_id"])
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])
        subscription = db.get(Subscription, subscription_id)
        wallet_before = (
            subscription.quota_credits_used,
            subscription.quota_credits_reserved,
        )
    payload = {
        "name": "Must be manual",
        "source_audio_asset_id": asset_id,
        "consent_confirmed": True,
    }
    if provider is not None:
        payload["provider"] = provider

    response = TestClient(app).post(
        "/api/v1/brand-voices",
        json=payload,
        headers={**auth_context["headers"], **billing_headers},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DOUBAO_MANUAL_ORDER_REQUIRED"
    with auth_db() as db:
        subscription = db.get(Subscription, subscription_id)
        assert db.scalar(select(BrandVoice)) is None
        assert db.scalar(select(BillingOperation)) is None
        assert db.scalar(select(UsageRecord)) is None
        assert (
            subscription.quota_credits_used,
            subscription.quota_credits_reserved,
        ) == wallet_before


def test_create_brand_voice_rejects_unknown_provider(auth_context, auth_db):
    with auth_db() as db:
        _seed_brand_voice_billing(
            db,
            auth_context["tenant_id"],
            huading_access=False,
        )
        asset_id = _seed_audio_asset(db, auth_context["tenant_id"])

    client = TestClient(app)
    resp = client.post(
        "/api/v1/brand-voices",
        json={
            "name": "Store Voice",
            "source_audio_asset_id": asset_id,
            "consent_confirmed": True,
            "provider": "unknown",
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 422


def test_delete_brand_voice_rejects_awaiting_manual_renewal(auth_context, auth_db):
    now = datetime.now(UTC)
    with auth_db() as db:
        subscription = db.scalar(select(Subscription))
        db.get(Plan, subscription.plan_id).code = "huading"
        subscription.quota_credits_total = 100_000
        voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            owner_user_id=auth_context["user_id"],
            name="Expired renewal target",
            provider="doubao-voice-clone",
            speaker_id="manual-renewal-target",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=now - timedelta(days=366),
            activated_at=now - timedelta(days=366),
            expires_at=now - timedelta(days=1),
        )
        asset = Asset(
            tenant_id=auth_context["tenant_id"],
            type="audio",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/renewal.wav",
            mime_type="audio/wav",
            duration_ms=10_000,
            status="ready",
        )
        db.add_all([voice, asset])
        db.commit()
        voice_id = voice.id
        asset_id = asset.id

    client = TestClient(app)
    payload = {
        "order_type": "renew",
        "requested_name": "Renewed voice",
        "source_audio_asset_id": asset_id,
        "consent_confirmed": True,
        "existing_brand_voice_id": voice_id,
    }
    quote = client.post(
        "/api/v1/brand-voice-orders/estimate",
        headers=auth_context["headers"],
        json=payload,
    ).json()["data"]
    submitted = client.post(
        "/api/v1/brand-voice-orders",
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote["quote_token"],
        },
        json=payload,
    )
    assert submitted.status_code == 201

    deleted = client.delete(
        f"/api/v1/brand-voices/{voice_id}",
        headers=auth_context["headers"],
    )

    assert deleted.status_code == 409
    assert deleted.json()["error"]["code"] == "BRAND_VOICE_ORDER_NOT_CANCELLABLE"
    with auth_db() as db:
        assert db.get(BrandVoice, voice_id).deleted_at is None


def test_clone_http_error_details_truncates_long_response_text():
    from app.api.v1.routes import brand_voices as brand_voice_routes

    class _LongHttpResponse:
        status_code = 429
        text = "x" * 1200

    class _LongHttpError(Exception):
        response = _LongHttpResponse()

    details = brand_voice_routes._http_error_details(_LongHttpError())

    assert details["http_status_code"] == 429
    assert details["http_response_text"] == f"{'x' * 1000}..."


def test_create_brand_voice_rejects_cross_tenant_source_audio(auth_context, auth_db, monkeypatch):
    from app.api.v1.routes import brand_voices as brand_voice_routes

    provider = _CloneProvider()
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve_named_provider",
        lambda _db, *, tenant_id, capability, provider: provider_obj,
    )
    provider_obj = provider
    other_tenant_id = "other-source-audio-tenant"
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        db.add(Tenant(id=other_tenant_id, slug="other-source-audio", name="Other Audio"))
        db.flush()
        other_asset_id = _seed_audio_asset(db, other_tenant_id)

    client = TestClient(app)
    resp = client.post(
        "/api/v1/brand-voices/estimate",
        json={
            "name": "Store Voice",
            "source_audio_asset_id": other_asset_id,
            "consent_confirmed": True,
            "provider": "cosyvoice",
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SOURCE_AUDIO_ASSET_NOT_FOUND"
    assert provider.clone_calls == []
    with auth_db() as db:
        assert db.scalar(select(UsageRecord).where(UsageRecord.capability == "voice_clone")) is None


def test_brand_voice_list_and_delete_are_tenant_scoped_404(
    auth_context,
    auth_db,
    monkeypatch,
):
    from app.api.v1.routes import brand_voices as brand_voice_routes

    provider = _CloneProvider()
    monkeypatch.setattr(
        brand_voice_routes,
        "resolve_named_provider",
        lambda _db, *, tenant_id, capability, provider: provider_obj,
    )
    provider_obj = provider
    other_tenant_id = "other-brand-tenant"
    with auth_db() as db:
        db.add(Tenant(id=other_tenant_id, slug="other-brand", name="Other Brand"))
        own = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Own Voice",
            source_audio_asset_id=_seed_audio_asset(db, auth_context["tenant_id"]),
            provider="cosyvoice-voice-clone",
            speaker_id="own-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        other = BrandVoice(
            tenant_id=other_tenant_id,
            name="Other Voice",
            source_audio_asset_id=None,
            provider="cosyvoice-voice-clone",
            speaker_id="other-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        db.add_all(
            [
                own,
                other,
                ProviderConfig(
                    tenant_id=None,
                    capability="voice_clone",
                    provider="doubao-voice-clone",
                    config={
                        "speaker_ids": ["own-speaker"],
                        "used_speaker_ids": {"own-speaker": "pending"},
                    },
                    is_active=True,
                ),
            ]
        )
        db.commit()
        own_id = own.id
        other_id = other.id
        config = db.scalar(select(ProviderConfig).where(ProviderConfig.capability == "voice_clone"))
        config.config = {
            "speaker_ids": ["own-speaker"],
            "used_speaker_ids": {"own-speaker": own_id},
        }
        db.commit()

    client = TestClient(app)
    list_resp = client.get("/api/v1/brand-voices", headers=auth_context["headers"])
    assert list_resp.status_code == 200
    assert [item["id"] for item in list_resp.json()["data"]["items"]] == [own_id]

    cross_delete = client.delete(
        f"/api/v1/brand-voices/{other_id}",
        headers=auth_context["headers"],
    )
    assert cross_delete.status_code == 404

    own_delete = client.delete(
        f"/api/v1/brand-voices/{own_id}",
        headers=auth_context["headers"],
    )
    assert own_delete.status_code == 204
    assert provider.delete_calls == [
        {
            "tenant_id": auth_context["tenant_id"],
            "brand_voice_id": own_id,
            "speaker_id": "own-speaker",
            "voice_clone_provider": "cosyvoice-voice-clone",
        }
    ]
    with auth_db() as db:
        assert db.get(BrandVoice, own_id).deleted_at is not None
        assert db.get(BrandVoice, other_id).deleted_at is None
        config = db.scalar(select(ProviderConfig).where(ProviderConfig.capability == "voice_clone"))
        assert config.config["used_speaker_ids"] == {"own-speaker": own_id}


def test_delete_brand_voice_uses_stored_provider(auth_context, auth_db, monkeypatch):
    from app.api.v1.routes import brand_voices as brand_voice_routes

    calls: list[tuple[str, str]] = []
    provider = _CloneProvider("cosyvoice-voice-clone")

    def fake_resolve_named(_db, *, tenant_id: str, capability: str, provider: str):
        calls.append((capability, provider))
        return provider_obj

    provider_obj = provider
    monkeypatch.setattr(brand_voice_routes, "resolve_named_provider", fake_resolve_named)
    with auth_db() as db:
        brand_voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Cosy Voice",
            provider="cosyvoice-voice-clone",
            speaker_id="cosy-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        db.add(brand_voice)
        db.commit()
        brand_voice_id = brand_voice.id

    client = TestClient(app)
    resp = client.delete(
        f"/api/v1/brand-voices/{brand_voice_id}",
        headers=auth_context["headers"],
    )

    assert resp.status_code == 204
    assert calls == [("voice_clone", "cosyvoice-voice-clone")]
    assert provider.delete_calls == [
        {
            "tenant_id": auth_context["tenant_id"],
            "brand_voice_id": brand_voice_id,
            "speaker_id": "cosy-speaker",
            "voice_clone_provider": "cosyvoice-voice-clone",
        }
    ]


def test_brand_voice_get_and_patch_are_tenant_scoped(auth_context, auth_db):
    other_tenant_id = "other-brand-crud-tenant"
    with auth_db() as db:
        db.add(Tenant(id=other_tenant_id, slug="other-brand-crud", name="Other CRUD"))
        db.flush()
        own = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Own Voice",
            provider="cosyvoice-voice-clone",
            speaker_id="own-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        other = BrandVoice(
            tenant_id=other_tenant_id,
            name="Other Voice",
            provider="cosyvoice-voice-clone",
            speaker_id="other-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        db.add_all([own, other])
        db.commit()
        own_id = own.id
        other_id = other.id

    client = TestClient(app)
    get_resp = client.get(f"/api/v1/brand-voices/{own_id}", headers=auth_context["headers"])
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["name"] == "Own Voice"
    assert get_resp.json()["data"]["provider"] == "cosyvoice-voice-clone"

    patch_resp = client.patch(
        f"/api/v1/brand-voices/{own_id}",
        json={"name": "Renamed Voice"},
        headers=auth_context["headers"],
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["data"]["name"] == "Renamed Voice"

    cross_get = client.get(
        f"/api/v1/brand-voices/{other_id}",
        headers=auth_context["headers"],
    )
    cross_patch = client.patch(
        f"/api/v1/brand-voices/{other_id}",
        json={"name": "Should Not Rename"},
        headers=auth_context["headers"],
    )
    assert cross_get.status_code == 404
    assert cross_patch.status_code == 404
    with auth_db() as db:
        assert db.get(BrandVoice, own_id).name == "Renamed Voice"
        assert db.get(BrandVoice, other_id).name == "Other Voice"


def test_voices_catalog_merges_ready_brand_voices_only(auth_context, auth_db):
    other_tenant_id = "other-catalog-tenant"
    with auth_db() as db:
        db.add(Tenant(id=other_tenant_id, slug="other-catalog", name="Other Catalog"))
        db.flush()
        preset = Voice(
            provider="edge-tts",
            voice_code="zh-CN-XiaoxiaoNeural",
            display_name="Xiaoxiao",
            gender="female",
            language="zh-CN",
            is_active=True,
        )
        ready_brand = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Ready Brand",
            provider="cosyvoice-voice-clone",
            speaker_id="ready-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        failed_brand = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Failed Brand",
            provider="cosyvoice-voice-clone",
            speaker_id=None,
            status="failed",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        other_brand = BrandVoice(
            tenant_id=other_tenant_id,
            name="Other Brand",
            provider="cosyvoice-voice-clone",
            speaker_id="other-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        db.add_all([preset, ready_brand, failed_brand, other_brand])
        db.commit()
        preset_id = preset.id
        ready_brand_id = ready_brand.id

    client = TestClient(app)
    resp = client.get("/api/v1/voices", headers=auth_context["headers"])

    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    preset_item = next(item for item in items if item["id"] == preset_id)
    assert preset_item["source"] == "preset"
    brand_items = [item for item in items if item["source"] == "brand_voice"]
    assert brand_items == [
        {
            "id": ready_brand_id,
            "provider": "cosyvoice-voice-clone",
            "voice_code": ready_brand_id,
            "display_name": "Ready Brand",
            "gender": "neutral",
            "language": "zh-CN",
            "sample_url": None,
            "source": "brand_voice",
        }
    ]
    assert all("speaker_id" not in item for item in items)


def test_avatar_talk_accepts_brand_voice_and_worker_uses_speaker_id(
    auth_context,
    auth_db,
    monkeypatch,
    tmp_path: Path,
):
    captured_tts_payloads: list[dict[str, Any]] = []
    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        avatar_id = _seed_avatar_asset(db, auth_context["tenant_id"])
        brand_voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            owner_user_id=auth_context["user_id"],
            name="Oral Brand",
            provider="doubao-voice-clone",
            speaker_id="oral-brand-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
            activated_at=datetime.now(UTC) - timedelta(days=1),
            expires_at=datetime.now(UTC) + timedelta(days=364),
        )
        db.add(brand_voice)
        db.commit()
        brand_voice_id = brand_voice.id

    monkeypatch.setattr(
        "app.api.v1.routes.videos.generate_avatar_talk_task.apply_async",
        lambda *args, **kwargs: None,
    )
    client = TestClient(app)
    payload = {
        "topic": "brand voice oral demo",
        "script": "hello from brand voice",
        "voice_id": brand_voice_id,
        "avatar_asset_id": avatar_id,
        "video_mode": "avatar_talk",
    }
    quote_resp = client.post(
        "/api/v1/videos/estimate",
        json=payload,
        headers=auth_context["headers"],
    )
    assert quote_resp.status_code == 200
    create_resp = client.post(
        "/api/v1/videos",
        json=payload,
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote_resp.json()["data"]["quote_token"],
        },
    )

    assert create_resp.status_code == 202
    unit_id = create_resp.json()["data"]["id"]
    with auth_db() as db:
        task = db.get(VideoTask, unit_id)
        assert task.voice_id is None
        assert task.brand_voice_id == brand_voice_id
        assert task.params["voice_source"] == "brand_voice"
        assert task.params["brand_voice_id"] == brand_voice_id
        assert task.params["tts_speaker_id"] == "oral-brand-speaker"

        class _FakeTTS:
            async def synthesize_speech(self, payload: dict[str, Any]):
                captured_tts_payloads.append(dict(payload))
                audio = tmp_path / "brand-voice.mp3"
                audio.write_bytes(b"MP3")
                return {
                    "audio_path": str(audio),
                    "timeline": [{"text": "hello", "start_ms": 0, "end_ms": 1000}],
                    "duration_ms": 1000,
                }

        monkeypatch.setattr(
            avatar_talk,
            "resolve",
            lambda _db, *, tenant_id, capability: _FakeTTS(),
        )
        monkeypatch.setattr(avatar_talk, "_work_dir", lambda _unit_id: tmp_path)
        monkeypatch.setattr(avatar_talk, "_audio_duration_sec", lambda _path: 1.0)
        monkeypatch.setattr(avatar_talk, "label_artifact_bytes", _passthrough_label)

        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=auth_context["tenant_id"],
            db=db,
            store=_Store(),
            storage=_Storage(),
        )
        avatar_talk.tts_step(ctx)

    assert captured_tts_payloads[0]["voice"] == "oral-brand-speaker"
    assert captured_tts_payloads[0]["voice_source"] == "brand_voice"


def test_video_submit_persists_its_single_trusted_voice_gate_timestamp(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    from app.api.v1.routes import videos as videos_route

    with auth_db() as db:
        _seed_brand_voice_billing(db, auth_context["tenant_id"])
        avatar_id = _seed_avatar_asset(db, auth_context["tenant_id"])
        submitted_at = datetime.now(UTC)
        voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            owner_user_id=auth_context["user_id"],
            name="Trusted submit time voice",
            provider="doubao-voice-clone",
            speaker_id="trusted-submit-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=submitted_at,
            activated_at=submitted_at - timedelta(days=1),
            expires_at=submitted_at + timedelta(hours=1),
        )
        db.add(voice)
        db.commit()
        voice_id = voice.id

    class _FixedDateTime:
        calls = 0

        @classmethod
        def now(cls, tz=None):
            cls.calls += 1
            return submitted_at

    client = TestClient(app)
    payload = {
        "topic": "trusted submit timestamp",
        "script": "accepted immediately before expiry",
        "voice_id": voice_id,
        "avatar_asset_id": avatar_id,
        "video_mode": "avatar_talk",
    }
    quote = client.post(
        "/api/v1/videos/estimate",
        json=payload,
        headers=auth_context["headers"],
    )
    assert quote.status_code == 200
    monkeypatch.setattr(videos_route, "datetime", _FixedDateTime)
    monkeypatch.setattr(
        videos_route.generate_avatar_talk_task,
        "apply_async",
        lambda **_kwargs: None,
    )
    response = client.post(
        "/api/v1/videos",
        json=payload,
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote.json()["data"]["quote_token"],
        },
    )

    assert response.status_code == 202
    with auth_db() as db:
        task = db.get(VideoTask, response.json()["data"]["task_id"])
        assert task.created_at.replace(tzinfo=UTC) == submitted_at
    assert _FixedDateTime.calls == 1


def test_avatar_talk_rejects_doubao_brand_voice_without_huading_access(
    auth_context,
    auth_db,
    monkeypatch,
):
    with auth_db() as db:
        _seed_brand_voice_billing(
            db,
            auth_context["tenant_id"],
            huading_access=False,
        )
        avatar_id = _seed_avatar_asset(db, auth_context["tenant_id"])
        user = db.get(User, auth_context["user_id"])
        assert user is not None
        user.role = "creator"
        brand_voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            owner_user_id=auth_context["user_id"],
            name="Premium Oral Voice",
            provider="doubao-voice-clone",
            speaker_id="premium-oral-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
            activated_at=datetime.now(UTC) - timedelta(days=1),
            expires_at=datetime.now(UTC) + timedelta(days=364),
        )
        db.add(brand_voice)
        db.commit()
        brand_voice_id = brand_voice.id

    monkeypatch.setattr(
        "app.api.v1.routes.videos.generate_avatar_talk_task.apply_async",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a rejected task must not be queued")
        ),
    )
    response = TestClient(app).post(
        "/api/v1/videos",
        json={
            "topic": "premium voice gate",
            "script": "this must not be queued",
            "voice_id": brand_voice_id,
            "avatar_asset_id": avatar_id,
            "video_mode": "avatar_talk",
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "VOICE_CLONE_PLAN_REQUIRED"
    with auth_db() as db:
        assert db.scalar(select(VideoTask)) is None


def test_seedance_i2v_rejects_doubao_brand_voice_without_huading_access(
    auth_context,
    auth_db,
    monkeypatch,
):
    with auth_db() as db:
        _seed_brand_voice_billing(
            db,
            auth_context["tenant_id"],
            huading_access=False,
        )
        user = db.get(User, auth_context["user_id"])
        assert user is not None
        user.role = "creator"
        brand_voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            owner_user_id=auth_context["user_id"],
            name="Ecommerce Premium Voice",
            provider="doubao-voice-clone",
            speaker_id="ecom-premium-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
            activated_at=datetime.now(UTC) - timedelta(days=1),
            expires_at=datetime.now(UTC) + timedelta(days=364),
        )
        db.add(brand_voice)
        db.commit()
        brand_voice_id = brand_voice.id

    monkeypatch.setattr(
        "app.api.v1.routes.videos.generate_seedance_i2v_task.apply_async",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a rejected task must not be queued")
        ),
    )
    response = TestClient(app).post(
        "/api/v1/videos",
        json={
            "topic": "premium ecommerce voice gate",
            "video_mode": "seedance_i2v",
            "product_image_keys": ["uploads/product.png"],
            "voice_id": brand_voice_id,
            "duration_sec": 15,
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "VOICE_CLONE_PLAN_REQUIRED"
    with auth_db() as db:
        assert db.scalar(select(VideoTask)) is None


def test_seedance_i2v_allows_cosyvoice_brand_voice_on_free_plan(
    auth_context,
    auth_db,
    monkeypatch,
):
    enqueued: dict[str, Any] = {}

    class _FakeI2VTask:
        def apply_async(self, *, args, task_id, queue=None):
            enqueued.update({"args": args, "task_id": task_id, "queue": queue})

    with auth_db() as db:
        _seed_brand_voice_billing(
            db,
            auth_context["tenant_id"],
            huading_access=False,
        )
        db.add(
            CreditRate(
                tenant_id=None,
                capability="video",
                unit="second",
                credits_per_unit=Decimal("1.0000"),
            )
        )
        user = db.get(User, auth_context["user_id"])
        assert user is not None
        user.role = "creator"
        brand_voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Ecommerce Free Voice",
            provider="cosyvoice-voice-clone",
            speaker_id="cosy-ecom-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        db.add(brand_voice)
        db.commit()
        brand_voice_id = brand_voice.id

    monkeypatch.setattr(
        "app.api.v1.routes.videos.generate_seedance_i2v_task",
        _FakeI2VTask(),
    )
    storage = _Storage()
    storage.objects[f"tenants/{auth_context['tenant_id']}/uploads/product.png"] = b"image"
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        payload = {
            "topic": "free ecommerce voice",
            "script": "free ecommerce narration",
            "video_mode": "seedance_i2v",
            "product_image_keys": ["uploads/product.png"],
            "voice_id": brand_voice_id,
            "duration_sec": 15,
        }
        quote_response = client.post(
            "/api/v1/videos/estimate",
            json=payload,
            headers=auth_context["headers"],
        )
        assert quote_response.status_code == 200
        response = client.post(
            "/api/v1/videos",
            json=payload,
            headers={
                **auth_context["headers"],
                "Idempotency-Key": str(uuid4()),
                "X-Huading-Quote": quote_response.json()["data"]["quote_token"],
            },
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 202
    task_id = response.json()["data"]["id"]
    assert enqueued["task_id"] == task_id
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        assert task.voice_id is None
        assert task.brand_voice_id == brand_voice_id
        assert task.params["voice_source"] == "brand_voice"
        assert task.params["tts_speaker_id"] == "cosy-ecom-speaker"
        assert task.params["brand_voice_provider"] == "cosyvoice-voice-clone"


def test_avatar_talk_worker_rechecks_doubao_brand_voice_plan_access(
    auth_context,
    auth_db,
    monkeypatch,
):
    unit_id = "doubao-worker-plan-gate"
    with auth_db() as db:
        _seed_brand_voice_billing(
            db,
            auth_context["tenant_id"],
            huading_access=False,
        )
        user = db.get(User, auth_context["user_id"])
        assert user is not None
        user.role = "creator"
        brand_voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            owner_user_id=auth_context["user_id"],
            name="Worker Premium Voice",
            provider="doubao-voice-clone",
            speaker_id="worker-premium-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
            activated_at=datetime.now(UTC) - timedelta(days=1),
            expires_at=datetime.now(UTC) + timedelta(days=364),
        )
        db.add(brand_voice)
        db.flush()
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=auth_context["tenant_id"],
                created_by_user_id=user.id,
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                script="premium worker gate",
                brand_voice_id=brand_voice.id,
                params={
                    "brand_voice_id": brand_voice.id,
                    "tts_speaker_id": brand_voice.speaker_id,
                    "brand_voice_provider": "cosyvoice-voice-clone",
                },
            )
        )
        db.commit()

        monkeypatch.setattr(
            avatar_talk,
            "resolve",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("TTS provider must not be called")
            ),
        )
        monkeypatch.setattr(
            avatar_talk,
            "resolve_named_provider",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("spoofed task params must not select a provider")
            ),
        )
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=auth_context["tenant_id"],
            db=db,
            store=_Store(),
            storage=_Storage(),
        )
        with pytest.raises(AppError) as exc_info:
            avatar_talk.tts_step(ctx)

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "VOICE_CLONE_PLAN_REQUIRED"


def test_avatar_talk_cosyvoice_brand_voice_uses_cosyvoice_synthesizer(
    auth_context,
    auth_db,
    monkeypatch,
    tmp_path: Path,
):
    captured_tts_payloads: list[dict[str, Any]] = []
    unit_id = "cosyvoice-avatar-unit"
    script_text = "c" * 100
    with auth_db() as db:
        brand_voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Cosy Brand",
            provider="cosyvoice-voice-clone",
            speaker_id="cosy-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=datetime.now(UTC),
        )
        db.add(brand_voice)
        db.flush()
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=auth_context["tenant_id"],
                created_by_user_id=auth_context["user_id"],
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="cosy brand voice",
                script=script_text,
                brand_voice_id=brand_voice.id,
                params={
                    "voice_source": "brand_voice",
                    "brand_voice_id": brand_voice.id,
                    "tts_speaker_id": "cosy-speaker",
                },
            )
        )
        db.commit()

        class _FakeCosyVoice:
            async def synthesize_speech(self, payload: dict[str, Any]):
                captured_tts_payloads.append(dict(payload))
                audio = tmp_path / "cosy-voice.mp3"
                audio.write_bytes(b"MP3")
                return {
                    "audio_path": str(audio),
                    "timeline": [],
                    "duration_ms": 1000,
                    "provider": "cosyvoice-tts",
                    "model": "cosyvoice-v3.5-plus",
                    "characters": len(payload["text"]),
                }

        monkeypatch.setattr(
            avatar_talk,
            "resolve_named_provider",
            lambda _db, *, tenant_id, capability, provider: _FakeCosyVoice(),
        )
        monkeypatch.setattr(
            avatar_talk,
            "resolve",
            lambda _db, *, tenant_id, capability: (_ for _ in ()).throw(
                AssertionError("CosyVoice brand voices must not use generic TTS resolve")
            ),
        )
        monkeypatch.setattr(avatar_talk, "_work_dir", lambda _unit_id: tmp_path)
        monkeypatch.setattr(avatar_talk, "_audio_duration_sec", lambda _path: 1.0)
        monkeypatch.setattr(avatar_talk, "label_artifact_bytes", _passthrough_label)
        monkeypatch.setattr(
            avatar_talk.provider_costs.settings,
            "engine_seedtts_cny_per_char",
            Decimal("0.0003"),
            raising=False,
        )
        monkeypatch.setattr(
            avatar_talk.provider_costs.settings,
            "engine_cosyvoice_tts_cny_per_char",
            Decimal("0.00015"),
            raising=False,
        )

        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=auth_context["tenant_id"],
            db=db,
            store=_Store(),
            storage=_Storage(),
        )
        avatar_talk.tts_step(ctx)

        usage = db.scalar(select(UsageRecord).where(UsageRecord.capability == "tts"))
        assert usage is not None
        assert usage.provider == "cosyvoice-tts"
        assert usage.model == "cosyvoice-v3.5-plus"
        assert usage.unit == "char"
        assert usage.quantity == Decimal(len(script_text))
        assert usage.credits == Decimal("0")
        assert usage.cost_cents == 2

    assert captured_tts_payloads[0]["voice"] == "cosy-speaker"
    assert captured_tts_payloads[0]["voice_source"] == "brand_voice"
    assert captured_tts_payloads[0]["brand_voice_provider"] == "cosyvoice-voice-clone"


def test_avatar_talk_preset_voice_still_uses_voice_code(
    auth_context,
    auth_db,
    monkeypatch,
    tmp_path: Path,
):
    captured_tts_payloads: list[dict[str, Any]] = []
    unit_id = "preset-voice-unit"
    with auth_db() as db:
        voice = Voice(
            provider="edge-tts",
            voice_code="zh-CN-XiaoxiaoNeural",
            display_name="Xiaoxiao",
            gender="female",
            language="zh-CN",
            is_active=True,
        )
        db.add(voice)
        db.flush()
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=auth_context["tenant_id"],
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="preset voice",
                script="preset script",
                voice_id=voice.id,
            )
        )
        db.commit()

        class _FakeTTS:
            async def synthesize_speech(self, payload: dict[str, Any]):
                captured_tts_payloads.append(dict(payload))
                audio = tmp_path / "preset-voice.mp3"
                audio.write_bytes(b"MP3")
                return {
                    "audio_path": str(audio),
                    "timeline": [],
                    "duration_ms": 1000,
                }

        monkeypatch.setattr(
            avatar_talk,
            "resolve",
            lambda _db, *, tenant_id, capability: _FakeTTS(),
        )
        monkeypatch.setattr(avatar_talk, "_work_dir", lambda _unit_id: tmp_path)
        monkeypatch.setattr(avatar_talk, "_audio_duration_sec", lambda _path: 1.0)
        monkeypatch.setattr(avatar_talk, "label_artifact_bytes", _passthrough_label)

        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=auth_context["tenant_id"],
            db=db,
            store=_Store(),
            storage=_Storage(),
        )
        avatar_talk.tts_step(ctx)

    assert captured_tts_payloads[0]["voice"] == "zh-CN-XiaoxiaoNeural"
    assert captured_tts_payloads[0]["voice_source"] == "preset"
