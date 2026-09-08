from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models import (
    AdminAuditLog,
    Asset,
    BillingOperation,
    BrandVoice,
    BrandVoiceOrder,
    BrandVoiceProviderId,
    Plan,
    Subscription,
    UsageRecord,
    User,
    VideoTask,
)
from app.main import app
from app.services.billing_operations import (
    VideoTaskBillingResource,
    complete_failed,
    complete_succeeded,
)
from app.workers import avatar_talk


class _QueuedTask:
    def __init__(self) -> None:
        self.task_ids: list[str] = []

    def apply_async(self, *, args, task_id, queue=None):
        del args, queue
        self.task_ids.append(task_id)
        return type("Result", (), {"status": "PENDING"})()


class _ProgressStore:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def update(self, task_id: str, **fields) -> None:
        self.events.append({"task_id": task_id, **fields})


class _MemoryStorage:
    bucket = "voice-billing-acceptance"

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        del content, content_type
        return f"memory://{key}"

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        suffix = "&download=1" if download_filename else ""
        return f"https://storage.test/{key}?ttl={expires_in}{suffix}"

    def delete_object(self, key: str) -> None:
        self.deleted.append(key)


def _register_tenant(client: TestClient, *, slug: str, email: str) -> tuple[dict, dict[str, str]]:
    response = client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": slug,
            "tenant_name": f"{slug} tenant",
            "email": email,
            "password": "test-only-password",
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    return data, {"Authorization": f"Bearer {data['token']['access_token']}"}


def _disable_sqlite_foreign_keys_for_fulfilled_order(auth_db) -> None:
    """Allow fixture teardown to drop the intentional order/provider audit cycle."""

    with auth_db() as db:
        engine = db.get_bind()
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")


def _manual_resolution_snapshot(auth_db, *, order_id: str) -> dict[str, object]:
    """Capture terminal rows that a replay or conflict must not rewrite."""

    with auth_db() as db:
        order = db.get(BrandVoiceOrder, order_id)
        operation = db.get(BillingOperation, order.billing_operation_id)
        usages = list(
            db.scalars(
                select(UsageRecord)
                .where(UsageRecord.billing_operation_id == operation.id)
                .order_by(UsageRecord.id)
            )
        )
        audits = list(
            db.scalars(
                select(AdminAuditLog)
                .where(AdminAuditLog.target_id == order_id)
                .order_by(AdminAuditLog.id)
            )
        )
        voices = list(
            db.scalars(
                select(BrandVoice)
                .where(BrandVoice.tenant_id == order.tenant_id)
                .order_by(BrandVoice.id)
            )
        )
        provider_ids = list(
            db.scalars(
                select(BrandVoiceProviderId)
                .where(BrandVoiceProviderId.first_order_id == order_id)
                .order_by(BrandVoiceProviderId.id)
            )
        )
        return {
            "order": (
                order.status,
                order.fulfilled_brand_voice_id,
                order.fulfilled_provider_id,
                order.resolver_user_id,
                order.fulfilled_at,
                order.rejected_at,
                order.rejection_reason,
                order.updated_at,
            ),
            "operation": (
                operation.status,
                operation.completion_kind,
                operation.completed_at,
                operation.requested_credits,
                operation.settled_credits,
                operation.released_credits,
                operation.result_type,
                operation.result_id,
                deepcopy(operation.result_payload),
                operation.error_code,
                operation.error_http_status,
                deepcopy(operation.error_payload),
                operation.updated_at,
            ),
            "usages": [
                (usage.id, usage.status, usage.credits, usage.settled_at) for usage in usages
            ],
            "audits": [
                (
                    audit.id,
                    audit.action,
                    deepcopy(audit.before),
                    deepcopy(audit.after),
                    audit.created_at,
                )
                for audit in audits
            ],
            "voices": [
                (
                    voice.id,
                    voice.owner_user_id,
                    voice.speaker_id,
                    voice.status,
                    voice.activated_at,
                    voice.expires_at,
                    voice.updated_at,
                )
                for voice in voices
            ],
            "provider_ids": [
                (
                    provider_id.id,
                    provider_id.normalized_provider_id,
                    provider_id.kind,
                    provider_id.status,
                    provider_id.brand_voice_id,
                    provider_id.first_order_id,
                    provider_id.updated_at,
                )
                for provider_id in provider_ids
            ],
        }


@pytest.mark.parametrize(
    (
        "action",
        "resolution_payload",
        "conflict_payload",
        "completion_kind",
        "expected_used",
        "expected_voice_count",
    ),
    [
        pytest.param(
            "fulfill",
            {"action": "fulfill", "provider_voice_id": "qa-customer-voice"},
            {"action": "fulfill", "provider_voice_id": "qa-different-voice"},
            "succeeded",
            30_000,
            1,
            id="fulfill",
        ),
        pytest.param(
            "reject",
            {"action": "reject", "rejection_reason": "test audio rejected"},
            {"action": "reject", "rejection_reason": "different rejection"},
            "rejected",
            0,
            0,
            id="reject",
        ),
    ],
)
def test_manual_doubao_http_resolution_is_exactly_once_and_user_scoped(
    monkeypatch,
    auth_context,
    auth_db,
    action: str,
    resolution_payload: dict[str, str],
    conflict_payload: dict[str, str],
    completion_kind: str,
    expected_used: int,
    expected_voice_count: int,
) -> None:
    """Cover the real customer order -> platform-admin resolution HTTP boundary."""

    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "engine_platform_tenant_slugs", {"acme"})
    monkeypatch.setattr(settings, "engine_doubao_official_voice_ids", [])
    client = TestClient(app)
    customer, customer_headers = _register_tenant(
        client,
        slug=f"voice-customer-{action}",
        email=f"voice-customer-{action}@example.com",
    )
    customer_tenant_id = customer["tenant"]["id"]
    customer_user_id = customer["user"]["id"]
    asset_id = f"manual-{action}-audio"
    other_user_id = f"manual-{action}-other-user"
    with auth_db() as db:
        plan = db.scalar(select(Plan))
        plan.code = "huading"
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == customer_tenant_id)
        )
        subscription.quota_credits_total = 100_000
        db.add_all(
            [
                Asset(
                    id=asset_id,
                    tenant_id=customer_tenant_id,
                    type="audio",
                    source="upload",
                    storage_key=f"tenants/{customer_tenant_id}/uploads/{asset_id}.wav",
                    mime_type="audio/wav",
                    duration_ms=10_000,
                    status="ready",
                ),
                User(
                    id=other_user_id,
                    tenant_id=customer_tenant_id,
                    email=f"manual-{action}-other@example.com",
                    password_hash="hash",
                    role="creator",
                ),
            ]
        )
        db.commit()
    other_token = create_access_token(
        user_id=other_user_id,
        tenant_id=customer_tenant_id,
        role="creator",
    )
    other_headers = {"Authorization": f"Bearer {other_token}"}

    order_payload = {
        "order_type": "create",
        "requested_name": f"QA {action} voice",
        "source_audio_asset_id": asset_id,
        "consent_confirmed": True,
    }
    quote = client.post(
        "/api/v1/brand-voice-orders/estimate",
        headers=customer_headers,
        json=order_payload,
    )
    assert quote.status_code == 200, quote.text
    assert quote.json()["data"]["payable_credits"] == 30_000
    submit_headers = {
        **customer_headers,
        "Idempotency-Key": str(uuid4()),
        "X-Huading-Quote": quote.json()["data"]["quote_token"],
    }
    submitted = client.post(
        "/api/v1/brand-voice-orders",
        headers=submit_headers,
        json=order_payload,
    )
    submit_replay = client.post(
        "/api/v1/brand-voice-orders",
        headers=submit_headers,
        json=order_payload,
    )
    assert submitted.status_code == 201, submitted.text
    assert submit_replay.status_code == 201, submit_replay.text
    submitted_data = submitted.json()["data"]
    replay_data = submit_replay.json()["data"]
    for timestamp_field in ("created_at", "updated_at"):
        assert replay_data[timestamp_field].removesuffix("Z") == submitted_data[
            timestamp_field
        ].removesuffix("Z")
        replay_data.pop(timestamp_field)
        submitted_data.pop(timestamp_field)
    assert replay_data == submitted_data
    order_id = submitted.json()["data"]["id"]

    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == customer_tenant_id)
        )
        assert subscription.quota_credits_reserved == 30_000
        assert subscription.quota_credits_used == 0
        assert db.scalar(select(func.count()).select_from(BillingOperation)) == 1
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 1

    customer_list = client.get("/api/v1/brand-voice-orders", headers=customer_headers)
    other_list = client.get("/api/v1/brand-voice-orders", headers=other_headers)
    cross_tenant_list = client.get(
        "/api/v1/brand-voice-orders",
        headers=auth_context["headers"],
    )
    assert customer_list.status_code == 200
    assert other_list.status_code == 200
    assert cross_tenant_list.status_code == 200
    assert {item["id"] for item in customer_list.json()["data"]["items"]} == {order_id}
    assert other_list.json()["data"]["items"] == []
    assert cross_tenant_list.json()["data"]["items"] == []
    assert (
        client.get(f"/api/v1/brand-voice-orders/{order_id}", headers=other_headers).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/brand-voice-orders/{order_id}", headers=auth_context["headers"]
        ).status_code
        == 404
    )

    resolve_path = f"/api/v1/admin/console/brand-voice-orders/{order_id}/resolve"
    resolved = client.post(
        resolve_path,
        headers=auth_context["headers"],
        json=resolution_payload,
    )
    assert resolved.status_code == 200, resolved.text
    terminal_snapshot = _manual_resolution_snapshot(auth_db, order_id=order_id)
    replayed = client.post(
        resolve_path,
        headers=auth_context["headers"],
        json=resolution_payload,
    )
    conflicted = client.post(
        resolve_path,
        headers=auth_context["headers"],
        json=conflict_payload,
    )
    opposite_payload = (
        {"action": "reject", "rejection_reason": "late opposite action"}
        if action == "fulfill"
        else {"action": "fulfill", "provider_voice_id": "qa-late-opposite-voice"}
    )
    opposite_conflict = client.post(
        resolve_path,
        headers=auth_context["headers"],
        json=opposite_payload,
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["data"] == resolved.json()["data"]
    assert conflicted.status_code == 409, conflicted.text
    assert conflicted.json()["error"]["code"] == "BRAND_VOICE_ORDER_ALREADY_RESOLVED"
    assert opposite_conflict.status_code == 409, opposite_conflict.text
    assert opposite_conflict.json()["error"]["code"] == "BRAND_VOICE_ORDER_ALREADY_RESOLVED"
    assert _manual_resolution_snapshot(auth_db, order_id=order_id) == terminal_snapshot

    fulfilled_voice_id: str | None = None
    with auth_db() as db:
        order = db.get(BrandVoiceOrder, order_id)
        operation = db.get(BillingOperation, order.billing_operation_id)
        usage = db.scalar(
            select(UsageRecord).where(UsageRecord.billing_operation_id == operation.id)
        )
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == customer_tenant_id)
        )
        assert operation.completion_kind == completion_kind
        assert operation.requested_credits == 30_000
        assert operation.settled_credits == expected_used
        assert operation.released_credits == 30_000 - expected_used
        assert usage.status == ("settled" if action == "fulfill" else "released")
        assert subscription.quota_credits_reserved == 0
        assert subscription.quota_credits_used == expected_used
        assert order.tenant_id == customer_tenant_id
        assert order.user_id == customer_user_id
        assert db.scalar(select(func.count()).select_from(AdminAuditLog)) == 1
        assert db.scalar(select(func.count()).select_from(BrandVoice)) == expected_voice_count
        assert (
            db.scalar(select(func.count()).select_from(BrandVoiceProviderId))
            == expected_voice_count
        )
        if action == "fulfill":
            voice = db.get(BrandVoice, order.fulfilled_brand_voice_id)
            fulfilled_voice_id = voice.id
            assert voice.tenant_id == customer_tenant_id
            assert voice.owner_user_id == customer_user_id
            assert voice.speaker_id == resolution_payload["provider_voice_id"]

    if action == "fulfill":
        assert (
            client.get(
                f"/api/v1/brand-voices/{fulfilled_voice_id}", headers=customer_headers
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/brand-voices/{fulfilled_voice_id}", headers=other_headers
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/brand-voices/{fulfilled_voice_id}",
                headers=auth_context["headers"],
            ).status_code
            == 404
        )
        _disable_sqlite_foreign_keys_for_fulfilled_order(auth_db)


def test_official_doubao_voice_is_platform_only_and_cross_tenant_unbilled(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    """An official platform voice never becomes a customer account entitlement."""

    official_speaker_id = "qa-official-platform-voice"
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "engine_platform_tenant_slugs", {"acme"})
    monkeypatch.setattr(
        settings,
        "engine_doubao_official_voice_ids",
        [official_speaker_id],
    )
    client = TestClient(app)
    customer, customer_headers = _register_tenant(
        client,
        slug="official-boundary-customer",
        email="official-boundary-customer@example.com",
    )
    customer_tenant_id = customer["tenant"]["id"]
    official_voice_id = "qa-official-platform-row"
    with auth_db() as db:
        db.add_all(
            [
                BrandVoice(
                    id=official_voice_id,
                    tenant_id=auth_context["tenant_id"],
                    name="Platform official voice",
                    provider="doubao-voice-clone",
                    speaker_id=official_speaker_id,
                    status="ready",
                    consent_confirmed=True,
                    consent_confirmed_at=datetime.now(UTC),
                ),
                BrandVoiceProviderId(
                    provider="doubao-voice-clone",
                    normalized_provider_id=official_speaker_id,
                    kind="official",
                    status="active",
                ),
                Asset(
                    id="official-boundary-avatar",
                    tenant_id=customer_tenant_id,
                    type="avatar_image",
                    source="upload",
                    storage_key=(
                        f"tenants/{customer_tenant_id}/uploads/official-boundary-avatar.png"
                    ),
                    mime_type="image/png",
                    status="ready",
                ),
            ]
        )
        db.commit()
        customer_subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == customer_tenant_id)
        )
        wallet_before = (
            customer_subscription.quota_credits_total,
            customer_subscription.quota_credits_used,
            customer_subscription.quota_credits_reserved,
        )

    platform_catalog = client.get("/api/v1/voices", headers=auth_context["headers"])
    customer_catalog = client.get("/api/v1/voices", headers=customer_headers)
    customer_detail = client.get(
        f"/api/v1/brand-voices/{official_voice_id}",
        headers=customer_headers,
    )
    customer_estimate = client.post(
        "/api/v1/videos/estimate",
        headers=customer_headers,
        json={
            "video_mode": "avatar_talk",
            "topic": "must stay isolated",
            "script": "must stay isolated",
            "voice_id": official_voice_id,
            "avatar_asset_id": "official-boundary-avatar",
        },
    )

    assert platform_catalog.status_code == 200
    assert official_voice_id in {item["id"] for item in platform_catalog.json()["data"]["items"]}
    assert customer_catalog.status_code == 200
    assert official_voice_id not in {
        item["id"] for item in customer_catalog.json()["data"]["items"]
    }
    assert customer_detail.status_code == 404
    assert customer_estimate.status_code == 404
    assert customer_estimate.json()["error"]["code"] == "VOICE_NOT_FOUND"
    with auth_db() as db:
        official_voice = db.get(BrandVoice, official_voice_id)
        customer_subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == customer_tenant_id)
        )
        assert official_voice.owner_user_id is None
        assert official_voice.activated_at is None
        assert official_voice.expires_at is None
        assert (
            customer_subscription.quota_credits_total,
            customer_subscription.quota_credits_used,
            customer_subscription.quota_credits_reserved,
        ) == wallet_before
        assert db.scalar(select(func.count()).select_from(BillingOperation)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0
        assert db.scalar(select(func.count()).select_from(BrandVoiceOrder)) == 0


def _submit_cosy_parent_video(
    *,
    monkeypatch,
    auth_context,
    auth_db,
) -> tuple[str, str, str, _QueuedTask]:
    from app.api.v1.routes import videos as videos_route

    queued = _QueuedTask()
    monkeypatch.setattr(videos_route, "generate_avatar_talk_task", queued)
    # Keep the text long enough that the configured CNY-per-character supplier
    # rate rounds to a non-zero integer cent in persisted telemetry.
    script = "计费验收" * 20
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"],
                Subscription.status == "active",
            )
        )
        subscription.quota_credits_total = 100_000
        subscription.quota_credits_used = 0
        subscription.quota_credits_reserved = 0
        voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Cosy acceptance voice",
            provider="cosyvoice-voice-clone",
            speaker_id="cosy-acceptance-speaker",
            status="ready",
            consent_confirmed=True,
        )
        avatar = Asset(
            tenant_id=auth_context["tenant_id"],
            type="avatar_image",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/cosy-acceptance.png",
            mime_type="image/png",
            status="ready",
        )
        db.add_all([voice, avatar])
        db.commit()
        voice_id, avatar_id = voice.id, avatar.id

    payload = {
        "video_mode": "avatar_talk",
        "topic": "Cosy billing acceptance",
        "script": script,
        "voice_id": voice_id,
        "avatar_asset_id": avatar_id,
    }
    client = TestClient(app)
    quote = client.post(
        "/api/v1/videos/estimate",
        headers=auth_context["headers"],
        json=payload,
    )
    assert quote.status_code == 200, quote.text
    tts_lines = [
        line
        for line in quote.json()["data"]["breakdown"]
        if (line["capability"], line["unit"]) == ("tts", "character")
    ]
    assert len(tts_lines) == 1
    assert tts_lines[0]["quantity"] == str(len(script))
    assert tts_lines[0]["unit_credits"] == "0.1000"
    created = client.post(
        "/api/v1/videos",
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote.json()["data"]["quote_token"],
        },
        json=payload,
    )
    assert created.status_code == 202, created.text
    task_id = created.json()["data"]["task_id"]
    operation_id = created.json()["data"]["billing"]["operation_id"]
    assert queued.task_ids == [task_id]
    with auth_db() as db:
        operation = db.get(BillingOperation, operation_id)
        task = db.get(VideoTask, task_id)
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == task.tenant_id)
        )
        usages = list(
            db.scalars(
                select(UsageRecord)
                .where(UsageRecord.billing_operation_id == operation_id)
                .order_by(UsageRecord.billing_item_index)
            )
        )
        tts_usage = next(usage for usage in usages if usage.capability == "tts")
        assert operation.status == "in_progress"
        assert operation.completion_kind is None
        assert operation.settled_credits == 0
        assert operation.released_credits == 0
        assert len(usages) == 2
        assert [usage.status for usage in usages] == ["reserved", "reserved"]
        assert sum((usage.credits for usage in usages), Decimal("0")) == Decimal(
            operation.requested_credits
        )
        assert subscription.quota_credits_used == 0
        assert subscription.quota_credits_reserved == operation.requested_credits
        assert tts_usage.quantity == Decimal(len(script))
        assert tts_usage.credits == Decimal(len(script)) * Decimal("0.1000")
    return task_id, operation_id, script, queued


def _billing_snapshot(auth_db, *, task_id: str, operation_id: str) -> dict[str, object]:
    with auth_db() as db:
        operation = db.get(BillingOperation, operation_id)
        task = db.get(VideoTask, task_id)
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == task.tenant_id)
        )
        usages = list(
            db.scalars(
                select(UsageRecord)
                .where(UsageRecord.billing_operation_id == operation_id)
                .order_by(UsageRecord.billing_item_index)
            )
        )
        return {
            "task": (task.status, task.error_code, task.storage_key),
            "operation": (
                operation.status,
                operation.completion_kind,
                str(operation.requested_credits),
                str(operation.settled_credits),
                str(operation.released_credits),
                operation.error_code,
                deepcopy(operation.error_payload),
            ),
            "wallet": (
                subscription.quota_credits_total,
                subscription.quota_credits_used,
                subscription.quota_credits_reserved,
            ),
            "usages": [
                (
                    usage.capability,
                    usage.unit,
                    str(usage.quantity),
                    str(usage.credits),
                    usage.status,
                    usage.provider,
                    usage.model,
                    usage.cost_cents,
                    deepcopy(usage.provider_usage),
                )
                for usage in usages
            ],
        }


@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
def test_cosy_parent_fake_tts_terminal_callback_is_exactly_once(
    monkeypatch,
    auth_context,
    auth_db,
    tmp_path: Path,
    terminal: str,
) -> None:
    """Run a fake Cosy TTS step, then prove settle/release and worker replay."""

    task_id, operation_id, script, _queued = _submit_cosy_parent_video(
        monkeypatch=monkeypatch,
        auth_context=auth_context,
        auth_db=auth_db,
    )
    provider_calls: list[dict[str, object]] = []

    class _FakeCosyTtsProvider:
        async def synthesize_speech(self, payload):
            provider_calls.append(dict(payload))
            audio_path = tmp_path / f"{task_id}.mp3"
            audio_path.write_bytes(b"fake-cosy-audio")
            return {
                "audio_path": str(audio_path),
                "duration_ms": 1_000,
                "timeline": [],
                "provider": "cosyvoice-tts",
                "model": "cosyvoice-v3.5-plus",
                "characters": len(payload["text"]),
            }

    progress = _ProgressStore()
    storage = _MemoryStorage()

    def deliver(ctx):
        ctx.storage_key = f"tenants/{ctx.tenant_id}/videos/{ctx.task_id}/final.mp4"
        ctx.size_bytes = 16
        return ctx

    def fail_downstream(_ctx):
        raise RuntimeError("fake downstream avatar supplier failed")

    monkeypatch.setattr(avatar_talk, "SessionLocal", auth_db)
    monkeypatch.setattr(avatar_talk, "build_progress_store", lambda _url: progress)
    monkeypatch.setattr(avatar_talk, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(
        avatar_talk,
        "_tts_provider_for_voice",
        lambda _db, *, tenant_id, brand_voice_provider: (
            _FakeCosyTtsProvider(),
            "voice_clone",
        ),
    )
    monkeypatch.setattr(avatar_talk, "_tail_faded_tts_audio", lambda path: path)
    monkeypatch.setattr(avatar_talk, "_audio_duration_sec", lambda _path: 1.0)
    monkeypatch.setattr(
        avatar_talk,
        "_apply_synthetic_label",
        lambda _ctx, content, *, kind, suffix: (content, {}),
    )
    monkeypatch.setattr(
        avatar_talk,
        "AVATAR_TALK_STEPS",
        [
            ("tts", 20, avatar_talk.tts_step),
            (
                "deliver" if terminal == "succeeded" else "avatar",
                98 if terminal == "succeeded" else 85,
                deliver if terminal == "succeeded" else fail_downstream,
            ),
        ],
    )

    first_call = avatar_talk.generate_avatar_talk_task.apply(
        args=[{"tenant_id": auth_context["tenant_id"], "video_task_id": task_id}],
        task_id=task_id,
    )
    if terminal == "succeeded":
        assert first_call.get()["status"] == "done"
        expected_worker_status = "done"
    else:
        with pytest.raises(RuntimeError, match="fake downstream avatar supplier failed"):
            first_call.get(propagate=True)
        expected_worker_status = "failed"

    after_first = _billing_snapshot(
        auth_db,
        task_id=task_id,
        operation_id=operation_id,
    )
    replay = avatar_talk.generate_avatar_talk_task.apply(
        args=[{"tenant_id": auth_context["tenant_id"], "video_task_id": task_id}],
        task_id=task_id,
    ).get()
    after_worker_replay = _billing_snapshot(
        auth_db,
        task_id=task_id,
        operation_id=operation_id,
    )

    assert replay == {"task_id": task_id, "status": expected_worker_status}
    assert after_worker_replay == after_first
    assert len(provider_calls) == 1
    assert provider_calls[0]["text"] == script
    assert provider_calls[0]["voice"] == "cosy-acceptance-speaker"
    assert provider_calls[0]["voice_source"] == "brand_voice"
    assert provider_calls[0]["brand_voice_provider"] == "cosyvoice-voice-clone"
    assert provider_calls[0]["task_id"] == task_id
    tts_usage = next(item for item in after_first["usages"] if item[0] == "tts")
    assert Decimal(tts_usage[3]) == Decimal(len(script)) * Decimal("0.1000")
    assert tts_usage[5:7] == ("cosyvoice-tts", "cosyvoice-v3.5-plus")
    assert tts_usage[8]["characters"] == len(script)
    assert tts_usage[8]["cost_cents"] == tts_usage[7]
    assert tts_usage[7] > 0
    assert after_first["wallet"][2] == 0
    assert after_first["operation"][0] == "completed"
    assert Decimal(after_first["operation"][2]) == (
        Decimal(after_first["operation"][3]) + Decimal(after_first["operation"][4])
    )

    if terminal == "succeeded":
        assert after_first["task"] == (
            "done",
            None,
            f"tenants/{auth_context['tenant_id']}/videos/{task_id}/final.mp4",
        )
        assert after_first["operation"][1] == "succeeded"
        assert after_first["operation"][5] is None
        assert after_first["wallet"][1] == int(Decimal(after_first["operation"][3]))
        assert after_first["wallet"][1] > 0
        assert [item[4] for item in after_first["usages"]] == ["settled", "settled"]
    else:
        assert after_first["task"] == ("failed", "AVATAR_TALK_FAILED", None)
        assert after_first["operation"][1] == "failed"
        assert after_first["operation"][5] == "AVATAR_TALK_FAILED"
        assert after_first["wallet"][1] == 0
        assert [item[4] for item in after_first["usages"]] == ["released", "released"]
        with auth_db() as db:
            repeated_failure = complete_failed(
                db,
                operation_id=operation_id,
                code="A_DIFFERENT_LATE_FAILURE",
                http_status=503,
                sanitized_detail=None,
            )
            late_success = complete_succeeded(
                db,
                operation_id=operation_id,
                actual_quantities={0: Decimal("1"), 1: Decimal(len(script))},
                result_type="video_task",
                result_id=task_id,
                result_payload=VideoTaskBillingResource(task_id=task_id, status="done"),
            )
            db.commit()
            assert repeated_failure.completion_kind == "failed"
            assert late_success.completion_kind == "failed"
        after_late_callbacks = _billing_snapshot(
            auth_db,
            task_id=task_id,
            operation_id=operation_id,
        )
        assert after_late_callbacks == after_first
