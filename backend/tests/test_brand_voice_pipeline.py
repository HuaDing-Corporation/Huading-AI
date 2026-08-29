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
        assert Decimal(
            str(operation.pricing_snapshot["disclosures"][0]["reference_unit_credits"])
        ) == Decimal("0.2000")


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

    assert failed.status_code == 502
    assert replay.status_code == 502
    assert provider.calls == 1
    with auth_db() as db:
        operation = db.scalar(select(BillingOperation))
        usage = db.scalar(select(UsageRecord))
        assert operation.status == "completed"
        assert operation.completion_kind == "failed"
        assert (operation.requested_credits, operation.released_credits) == (0, 0)
        assert usage.status == "released"
        assert usage.credits == 0


@pytest.mark.parametrize("provider", [None, "doubao", "doubao-voice-clone"])
def test_legacy_doubao_create_requires_manual_order_without_side_effects(
    auth_context,
    auth_db,
    monkeypatch,
    provider,
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
        headers=auth_context["headers"],
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
