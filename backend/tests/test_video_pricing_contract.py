from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.exceptions import AppError
from app.db.models import (
    Asset,
    BillingOperation,
    BrandVoice,
    CreditRate,
    Plan,
    Subscription,
    UsageRecord,
    User,
    VideoTask,
    Voice,
)
from app.main import app
from app.schemas.billing import BillingQuote
from app.schemas.videos import VideoGenerateRequest
from app.services.video_pricing import (
    build_video_estimate,
    normalize_billable_tts_text,
    resolve_effective_video_mode,
)


@pytest.mark.parametrize(
    ("literal_mode", "legacy_field", "expected"),
    [
        ("static_template", {"voice_id": "voice-1"}, "avatar_talk"),
        ("seedance_t2v", {"avatar_asset_id": "asset-1"}, "avatar_talk"),
        ("static_template", {}, "static_template"),
        ("seedance_t2v", {}, "seedance_t2v"),
        ("photo", {"voice_id": "voice-1"}, "photo"),
        ("video_gen", {"voice_id": "voice-1"}, "video_gen"),
    ],
)
def test_effective_mode_is_single_source_of_truth(
    literal_mode: str,
    legacy_field: dict[str, str],
    expected: str,
) -> None:
    base: dict[str, object] = {
        "video_mode": literal_mode,
        "topic": "正文",
        "script": "正文",
        **legacy_field,
    }
    if literal_mode == "photo":
        base["image_keys"] = ["uploads/ref.jpg"]
    if literal_mode == "video_gen":
        base["duration_sec"] = 5
    payload = VideoGenerateRequest.model_validate(base)

    assert resolve_effective_video_mode(payload) == expected


def test_billable_tts_text_is_stripped_once_and_keeps_punctuation() -> None:
    payload = VideoGenerateRequest.model_validate(
        {
            "video_mode": "avatar_talk",
            "topic": "正文",
            "voice_id": "voice-1",
            "avatar_asset_id": "asset-1",
            "script": "  你好，世界！  ",
        }
    )

    assert normalize_billable_tts_text(payload) == "你好，世界！"
    assert len(normalize_billable_tts_text(payload)) == 6


def test_billable_tts_text_rejects_missing_or_blank_script() -> None:
    payload = VideoGenerateRequest.model_validate(
        {
            "video_mode": "avatar_talk",
            "topic": "正文",
            "voice_id": "voice-1",
            "avatar_asset_id": "asset-1",
            "script": None,
        }
    )

    with pytest.raises(AppError) as caught:
        normalize_billable_tts_text(payload)

    assert caught.value.code == "BILLABLE_TEXT_REQUIRED"
    assert caught.value.status_code == 422


def test_cosyvoice_video_quote_has_exactly_one_character_line(db_session) -> None:
    user = db_session.get(User, "user-a")
    voice = BrandVoice(
        id="cosy-voice",
        tenant_id=user.tenant_id,
        name="Cosy",
        provider="cosyvoice-voice-clone",
        speaker_id="cosy-speaker",
        status="ready",
        consent_confirmed=True,
    )
    db_session.add(voice)
    db_session.commit()
    payload = VideoGenerateRequest.model_validate(
        {
            "video_mode": "avatar_talk",
            "topic": "正文",
            "voice_id": voice.id,
            "avatar_asset_id": "asset-1",
            "script": "  你好，世界！  ",
        }
    )

    estimate = build_video_estimate(db_session, user=user, payload=payload)

    assert isinstance(estimate, BillingQuote)
    assert estimate.pricing_contract == "billing_quote"
    assert estimate.pricing_shape == "composite"
    tts_lines = [line for line in estimate.breakdown if line.capability == "tts"]
    assert len(tts_lines) == 1
    assert tts_lines[0].unit == "character"
    assert tts_lines[0].quantity == "6"
    assert tts_lines[0].unit_credits == "0.1000"


@pytest.mark.parametrize(
    ("provider", "mode", "expected_base", "expected_tts_lines"),
    [
        pytest.param(
            "cosyvoice-voice-clone",
            "avatar_talk",
            ("avatar", "second", "3", "180.0000", "540.0000", "code_default"),
            1,
            id="cosyvoice-avatar-180",
        ),
        pytest.param(
            "doubao-voice-clone",
            "avatar_talk",
            ("avatar", "second", "3", "180.0000", "540.0000", "code_default"),
            0,
            id="doubao-avatar-180",
        ),
        pytest.param(
            "cosyvoice-voice-clone",
            "seedance_i2v",
            ("video", "second", "10", "100.0000", "1000.0000", "code_default"),
            1,
            id="cosyvoice-seedance-100",
        ),
        pytest.param(
            "doubao-voice-clone",
            "seedance_i2v",
            ("video", "second", "10", "100.0000", "1000.0000", "code_default"),
            0,
            id="doubao-seedance-100",
        ),
    ],
)
def test_brand_video_parent_line_uses_effective_mode_capability_and_fixed_composite_shape(
    db_session,
    provider: str,
    mode: str,
    expected_base: tuple[str, str, str, str, str, str],
    expected_tts_lines: int,
) -> None:
    user = db_session.get(User, "user-a")
    if provider == "doubao-voice-clone":
        db_session.get(Plan, "plan-a").code = "huading"
    voice = BrandVoice(
        id=f"{provider}-{mode}",
        tenant_id=user.tenant_id,
        owner_user_id=user.id,
        name="Brand voice",
        provider=provider,
        speaker_id=f"speaker-{provider}-{mode}",
        status="ready",
        consent_confirmed=True,
        activated_at=datetime.now(UTC) - timedelta(days=1),
        expires_at=datetime.now(UTC) + timedelta(days=364),
    )
    db_session.add(voice)
    db_session.commit()
    common = {
        "video_mode": mode,
        "topic": "正文",
        "voice_id": voice.id,
        "script": "123456789012345",
    }
    mode_fields = (
        {"avatar_asset_id": "asset-1"}
        if mode == "avatar_talk"
        else {
            "product_image_keys": ["uploads/product.png"],
            "scene_prompt": "产品展示",
            "duration_sec": 6,
            "resolution": "480p",
        }
    )

    estimate = build_video_estimate(
        db_session,
        user=user,
        payload=VideoGenerateRequest.model_validate({**common, **mode_fields}),
    )

    assert isinstance(estimate, BillingQuote)
    assert estimate.operation == "video_create"
    assert estimate.pricing_shape == "composite"
    assert (
        estimate.unit,
        estimate.quantity,
        estimate.unit_credits,
        estimate.rate_scope,
        estimate.rate_source,
    ) == (None, None, None, None, None)
    base_lines = [line for line in estimate.breakdown if line.capability != "tts"]
    assert len(base_lines) == 1
    base = base_lines[0]
    assert (
        base.capability,
        base.unit,
        base.quantity,
        base.unit_credits,
        base.subtotal_credits,
        base.rate_source.value,
    ) == expected_base
    tts_lines = [line for line in estimate.breakdown if line.capability == "tts"]
    assert len(tts_lines) == expected_tts_lines
    if tts_lines:
        assert (
            tts_lines[0].unit,
            tts_lines[0].quantity,
            tts_lines[0].unit_credits,
            tts_lines[0].subtotal_credits,
        ) == ("character", "15", "0.1000", "1.5000")


def test_brand_video_parent_rate_keeps_capability_specific_platform_and_tenant_provenance(
    db_session,
) -> None:
    user = db_session.get(User, "user-a")
    now = datetime.now(UTC)
    voice = BrandVoice(
        id="capability-specific-cosy",
        tenant_id=user.tenant_id,
        name="Cosy",
        provider="cosyvoice-voice-clone",
        speaker_id="capability-specific-speaker",
        status="ready",
        consent_confirmed=True,
    )
    rates = [
        CreditRate(
            id="platform-avatar-181",
            tenant_id=None,
            capability="avatar",
            unit="second",
            credits_per_unit=Decimal("181.0000"),
            effective_at=now,
        ),
        CreditRate(
            id="platform-video-101",
            tenant_id=None,
            capability="video",
            unit="second",
            credits_per_unit=Decimal("101.0000"),
            effective_at=now,
        ),
        CreditRate(
            id="tenant-video-102",
            tenant_id=user.tenant_id,
            capability="video",
            unit="second",
            credits_per_unit=Decimal("102.0000"),
            effective_at=now,
        ),
    ]
    db_session.add_all([voice, *rates])
    db_session.commit()

    avatar_quote = build_video_estimate(
        db_session,
        user=user,
        payload=VideoGenerateRequest.model_validate(
            {
                "video_mode": "avatar_talk",
                "topic": "正文",
                "voice_id": voice.id,
                "avatar_asset_id": "asset-1",
                "script": "123456789012345",
            }
        ),
    )
    seedance_quote = build_video_estimate(
        db_session,
        user=user,
        payload=VideoGenerateRequest.model_validate(
            {
                "video_mode": "seedance_i2v",
                "topic": "正文",
                "voice_id": voice.id,
                "product_image_keys": ["uploads/product.png"],
                "scene_prompt": "产品展示",
                "duration_sec": 6,
                "resolution": "480p",
                "script": "123456789012345",
            }
        ),
    )

    assert isinstance(avatar_quote, BillingQuote)
    assert isinstance(seedance_quote, BillingQuote)
    avatar_base = next(line for line in avatar_quote.breakdown if line.capability != "tts")
    seedance_base = next(line for line in seedance_quote.breakdown if line.capability != "tts")
    assert (
        avatar_base.capability,
        avatar_base.unit_credits,
        avatar_base.rate_source.value,
        avatar_base.rate_id,
    ) == ("avatar", "181.0000", "platform_rate", "platform-avatar-181")
    assert (
        seedance_base.capability,
        seedance_base.unit_credits,
        seedance_base.rate_source.value,
        seedance_base.rate_id,
    ) == ("video", "102.0000", "tenant_rate", "tenant-video-102")


def test_true_deferred_and_legacy_estimates_do_not_issue_tokens(db_session) -> None:
    user = db_session.get(User, "user-a")
    deferred = build_video_estimate(
        db_session,
        user=user,
        payload=VideoGenerateRequest.model_validate(
            {"video_mode": "static_template", "topic": "正文"}
        ),
    )
    legacy = build_video_estimate(
        db_session,
        user=user,
        payload=VideoGenerateRequest.model_validate({"video_mode": "photo", "topic": "产品图"}),
    )

    assert deferred.pricing_contract == "deferred_unpriced"
    assert deferred.estimated_credits == 0
    assert deferred.unpriced is True
    assert not hasattr(deferred, "quote_token")
    assert legacy.pricing_contract == "legacy_estimate"
    assert legacy.estimated_credits > 0
    assert not hasattr(legacy, "quote_token")


def test_doubao_brand_video_has_no_character_component(db_session) -> None:
    user = db_session.get(User, "user-a")
    db_session.get(Plan, "plan-a").code = "huading"
    voice = BrandVoice(
        id="doubao-voice",
        tenant_id=user.tenant_id,
        owner_user_id=user.id,
        name="Doubao",
        provider="doubao-voice-clone",
        speaker_id="doubao-speaker",
        status="ready",
        consent_confirmed=True,
        activated_at=datetime.now(UTC) - timedelta(days=1),
        expires_at=datetime.now(UTC) + timedelta(days=364),
    )
    db_session.add(voice)
    db_session.commit()
    payload = VideoGenerateRequest.model_validate(
        {
            "video_mode": "avatar_talk",
            "topic": "正文",
            "voice_id": voice.id,
            "avatar_asset_id": "asset-1",
            "script": "你好，世界！",
        }
    )

    estimate = build_video_estimate(db_session, user=user, payload=payload)

    assert isinstance(estimate, BillingQuote)
    assert estimate.pricing_shape == "composite"
    assert estimate.operation == "video_create"
    assert len(estimate.breakdown) == 1
    assert estimate.breakdown[0].capability == "avatar"
    assert estimate.breakdown[0].unit == "second"


def test_cosyvoice_brand_video_requires_frozen_script(auth_context, auth_db) -> None:
    with auth_db() as db:
        voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Cosy",
            provider="cosyvoice-voice-clone",
            speaker_id="cosy-speaker",
            status="ready",
            consent_confirmed=True,
        )
        db.add(voice)
        db.commit()
        voice_id = voice.id

    response = TestClient(app).post(
        "/api/v1/videos/estimate",
        headers=auth_context["headers"],
        json={
            "video_mode": "avatar_talk",
            "topic": "正文",
            "voice_id": voice_id,
            "avatar_asset_id": "asset-1",
            "script": None,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BILLABLE_TEXT_REQUIRED"


def test_brand_submit_commits_task_operation_and_allocations_before_enqueue(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Cosy",
            provider="cosyvoice-voice-clone",
            speaker_id="cosy-speaker",
            status="ready",
            consent_confirmed=True,
        )
        avatar = Asset(
            tenant_id=auth_context["tenant_id"],
            type="avatar_image",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/avatar.png",
            mime_type="image/png",
            status="ready",
        )
        db.add_all([voice, avatar])
        db.commit()
        voice_id, avatar_id = voice.id, avatar.id

    committed_before_enqueue: dict[str, object] = {}

    class _FakeTask:
        def apply_async(self, *, args, task_id, queue=None):
            with auth_db() as db:
                operation = db.scalar(
                    select(BillingOperation).where(BillingOperation.result_id == task_id)
                )
                task = db.get(VideoTask, task_id)
                usages = list(
                    db.scalars(
                        select(UsageRecord)
                        .where(UsageRecord.billing_operation_id == operation.id)
                        .order_by(UsageRecord.billing_item_index)
                    )
                )
                committed_before_enqueue.update(
                    task=task is not None,
                    operation=operation is not None,
                    usage_count=len(usages),
                    operation_id=operation.id,
                )
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_avatar_talk_task", _FakeTask())
    payload = {
        "video_mode": "avatar_talk",
        "topic": "正文",
        "voice_id": voice_id,
        "avatar_asset_id": avatar_id,
        "script": "  你好，世界！  ",
    }
    client = TestClient(app)
    quote = client.post("/api/v1/videos/estimate", headers=auth_context["headers"], json=payload)
    assert quote.status_code == 200, quote.text
    submission_headers = {
        **auth_context["headers"],
        "Idempotency-Key": str(uuid4()),
        "X-Huading-Quote": quote.json()["data"]["quote_token"],
    }
    response = client.post("/api/v1/videos", headers=submission_headers, json=payload)

    assert response.status_code == 202, response.text
    assert response.json()["data"]["pricing_contract"] == "billing_quote"
    assert response.json()["data"]["billing"]["status"] == "reserved"
    assert committed_before_enqueue == {
        "task": True,
        "operation": True,
        "usage_count": 2,
        "operation_id": response.json()["data"]["billing"]["operation_id"],
    }
    with auth_db() as db:
        task = db.get(VideoTask, response.json()["data"]["task_id"])
        usages = list(
            db.scalars(
                select(UsageRecord)
                .where(UsageRecord.video_task_id == task.id)
                .order_by(UsageRecord.billing_item_index)
            )
        )
    assert task.script == "你好，世界！"
    assert task.params["billing_tts_text"] == task.script
    assert [(usage.capability, usage.unit) for usage in usages] == [
        ("avatar", "second"),
        ("tts", "character"),
    ]

    with auth_db() as db:
        db.delete(db.get(BrandVoice, voice_id))
        db.commit()
    replay = client.post("/api/v1/videos", headers=submission_headers, json=payload)
    assert replay.status_code == 202, replay.text
    assert replay.json()["data"] == response.json()["data"]


def test_late_idempotency_replay_does_not_enqueue_existing_video_again(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Cosy",
            provider="cosyvoice-voice-clone",
            speaker_id="cosy-speaker",
            status="ready",
            consent_confirmed=True,
        )
        avatar = Asset(
            tenant_id=auth_context["tenant_id"],
            type="avatar_image",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/race-avatar.png",
            mime_type="image/png",
            status="ready",
        )
        db.add_all([voice, avatar])
        db.commit()
        voice_id, avatar_id = voice.id, avatar.id

    from app.api.v1.routes import videos as videos_route

    enqueued_task_ids: list[str] = []

    class _FakeTask:
        def apply_async(self, *, args, task_id, queue=None):
            enqueued_task_ids.append(task_id)
            return type("Result", (), {"status": "PENDING"})()

    monkeypatch.setattr(videos_route, "generate_avatar_talk_task", _FakeTask())
    payload = {
        "video_mode": "avatar_talk",
        "topic": "正文",
        "voice_id": voice_id,
        "avatar_asset_id": avatar_id,
        "script": "你好，世界！",
    }
    client = TestClient(app)
    quote = client.post("/api/v1/videos/estimate", headers=auth_context["headers"], json=payload)
    headers = {
        **auth_context["headers"],
        "Idempotency-Key": str(uuid4()),
        "X-Huading-Quote": quote.json()["data"]["quote_token"],
    }
    created = client.post("/api/v1/videos", headers=headers, json=payload)
    assert created.status_code == 202, created.text

    real_find_replay = videos_route.find_replay
    top_level_missed = False

    def miss_only_top_level_replay(*args, **kwargs):
        nonlocal top_level_missed
        if not top_level_missed:
            top_level_missed = True
            return None
        return real_find_replay(*args, **kwargs)

    monkeypatch.setattr(videos_route, "find_replay", miss_only_top_level_replay)
    replayed = client.post("/api/v1/videos", headers=headers, json=payload)

    assert replayed.status_code == 202, replayed.text
    assert replayed.json()["data"] == created.json()["data"]
    assert enqueued_task_ids == [created.json()["data"]["task_id"]]
    with auth_db() as db:
        assert db.query(VideoTask).count() == 1
        assert db.query(BillingOperation).count() == 1


def test_brand_video_enqueue_failure_completes_and_releases_operation(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            name="Cosy",
            provider="cosyvoice-voice-clone",
            speaker_id="cosy-speaker",
            status="ready",
            consent_confirmed=True,
        )
        avatar = Asset(
            tenant_id=auth_context["tenant_id"],
            type="avatar_image",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/enqueue-avatar.png",
            mime_type="image/png",
            status="ready",
        )
        db.add_all([voice, avatar])
        db.commit()
        voice_id, avatar_id = voice.id, avatar.id

    from app.api.v1.routes import videos as videos_route

    class _FailingTask:
        def apply_async(self, *, args, task_id, queue=None):
            raise RuntimeError("queue unavailable")

    monkeypatch.setattr(videos_route, "generate_avatar_talk_task", _FailingTask())
    payload = {
        "video_mode": "avatar_talk",
        "topic": "正文",
        "voice_id": voice_id,
        "avatar_asset_id": avatar_id,
        "script": "你好，世界！",
    }
    client = TestClient(app)
    quote = client.post("/api/v1/videos/estimate", headers=auth_context["headers"], json=payload)
    response = client.post(
        "/api/v1/videos",
        headers={
            **auth_context["headers"],
            "Idempotency-Key": str(uuid4()),
            "X-Huading-Quote": quote.json()["data"]["quote_token"],
        },
        json=payload,
    )

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "VIDEO_ENQUEUE_FAILED"
    with auth_db() as db:
        task = db.query(VideoTask).one()
        operation = db.query(BillingOperation).one()
        usages = db.query(UsageRecord).filter_by(billing_operation_id=operation.id).all()
        subscription = db.query(Subscription).filter_by(tenant_id=auth_context["tenant_id"]).one()
        assert task.status == "failed"
        assert task.error_code == "VIDEO_ENQUEUE_FAILED"
        assert operation.status == "completed"
        assert operation.completion_kind == "failed"
        assert operation.settled_credits == 0
        assert operation.released_credits == operation.requested_credits
        assert subscription.quota_credits_reserved == 0
        assert subscription.quota_credits_used == 0
        assert len(usages) == 2
        assert all(usage.status == "released" for usage in usages)


def test_cosyvoice_video_quote_preserves_mixed_rate_provenance(db_session) -> None:
    user = db_session.get(User, "user-a")
    avatar_rate = CreditRate(
        tenant_id=user.tenant_id,
        capability="avatar",
        unit="second",
        credits_per_unit=Decimal("125.0000"),
        effective_at=datetime.now(UTC),
    )
    tts_rate = CreditRate(
        tenant_id=None,
        capability="tts",
        unit="character",
        credits_per_unit=Decimal("0.2000"),
        effective_at=datetime.now(UTC),
    )
    voice = BrandVoice(
        tenant_id=user.tenant_id,
        name="Cosy",
        provider="cosyvoice-voice-clone",
        speaker_id="cosy-speaker",
        status="ready",
        consent_confirmed=True,
    )
    db_session.add_all([avatar_rate, tts_rate, voice])
    db_session.commit()

    quote = build_video_estimate(
        db_session,
        user=user,
        payload=VideoGenerateRequest.model_validate(
            {
                "video_mode": "avatar_talk",
                "topic": "正文",
                "voice_id": voice.id,
                "avatar_asset_id": "asset-1",
                "script": "你好，世界！",
            }
        ),
    )

    assert isinstance(quote, BillingQuote)
    provenance = [
        (line.capability, line.rate_source.value, line.rate_id)
        for line in quote.breakdown
    ]
    assert provenance == [
        ("avatar", "tenant_rate", avatar_rate.id),
        ("tts", "platform_rate", tts_rate.id),
    ]


def test_legacy_and_deferred_submissions_ignore_forged_billing_headers(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        voice = Voice(
            provider="doubao-seed-tts",
            voice_code="preset-voice",
            display_name="Preset",
        )
        avatar = Asset(
            tenant_id=auth_context["tenant_id"],
            type="avatar_image",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/preset-avatar.png",
            mime_type="image/png",
            status="ready",
        )
        db.add_all([voice, avatar])
        db.commit()
        voice_id, avatar_id = voice.id, avatar.id

    from app.api.v1.routes import videos as videos_route

    class _FakeTask:
        def apply_async(self, *, args, task_id, queue=None):
            return type("Result", (), {"status": "PENDING"})()

    monkeypatch.setattr(videos_route, "generate_avatar_talk_task", _FakeTask())
    monkeypatch.setattr(videos_route, "generate_video_task", _FakeTask())
    client = TestClient(app)
    forged_headers = {
        **auth_context["headers"],
        "Idempotency-Key": str(uuid4()),
        "X-Huading-Quote": "forged-quote-token",
    }
    legacy = client.post(
        "/api/v1/videos",
        headers=forged_headers,
        json={
            "video_mode": "avatar_talk",
            "topic": "正文",
            "voice_id": voice_id,
            "avatar_asset_id": avatar_id,
            "script": "预设音色文案",
        },
    )
    deferred = client.post(
        "/api/v1/videos",
        headers={**forged_headers, "Idempotency-Key": str(uuid4())},
        json={"video_mode": "static_template", "topic": "正文", "script": "文案"},
    )

    assert legacy.status_code == 202, legacy.text
    assert legacy.json()["data"]["pricing_contract"] == "legacy_estimate"
    assert deferred.status_code == 202, deferred.text
    assert deferred.json()["data"]["pricing_contract"] == "deferred_unpriced"
    with auth_db() as db:
        assert db.query(BillingOperation).count() == 0


def test_doubao_seedance_brand_video_runs_full_billing_lifecycle(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        db.query(Plan).one().code = "huading"
        voice = BrandVoice(
            tenant_id=auth_context["tenant_id"],
            owner_user_id=auth_context["user_id"],
            name="Doubao",
            provider="doubao-voice-clone",
            speaker_id="doubao-speaker",
            status="ready",
            consent_confirmed=True,
            activated_at=datetime.now(UTC) - timedelta(days=1),
            expires_at=datetime.now(UTC) + timedelta(days=364),
        )
        db.add(voice)
        db.commit()
        voice_id = voice.id

    from app.api.v1.routes import videos as videos_route
    from app.workers import avatar_talk

    queued: list[str] = []

    class _FakeTask:
        def apply_async(self, *, args, task_id, queue=None):
            queued.append(task_id)
            return type("Result", (), {"status": "PENDING"})()

    class _Store:
        def update(self, task_id, **fields):
            return None

    class _Storage:
        bucket = "bucket"

        def presign_get_url(self, key, *, expires_in, download_filename=None):
            return f"https://storage.test/{key}"

        def delete_object(self, key):
            return None

    monkeypatch.setattr(videos_route, "generate_seedance_i2v_task", _FakeTask())
    monkeypatch.setattr(videos_route, "_validate_product_image_storage_keys", lambda *a, **k: None)
    payload = {
        "video_mode": "seedance_i2v",
        "topic": "产品视频",
        "voice_id": voice_id,
        "product_image_keys": ["uploads/product.png"],
        "script": "品牌文案",
        "scene_prompt": "产品展示",
        "duration_sec": 6,
        "resolution": "480p",
    }
    client = TestClient(app)
    quote = client.post("/api/v1/videos/estimate", headers=auth_context["headers"], json=payload)
    assert quote.status_code == 200, quote.text
    assert quote.json()["data"]["pricing_contract"] == "billing_quote"
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
    assert queued == [task_id]

    def fake_step(ctx):
        # Two three-second Seedance scenes render into a two-second final mux;
        # billing must retain the provider's six seconds, not the output duration.
        ctx.duration_sec = 2
        ctx.seedance_billable_seconds = 6
        ctx.provider_cost_cents = 321
        ctx.storage_key = f"tenants/{auth_context['tenant_id']}/videos/{task_id}/final.mp4"
        return ctx

    monkeypatch.setattr(avatar_talk, "SessionLocal", auth_db)
    monkeypatch.setattr(avatar_talk, "build_progress_store", lambda _url: _Store())
    monkeypatch.setattr(avatar_talk, "create_object_storage", lambda _settings: _Storage())
    monkeypatch.setattr(avatar_talk, "ECOM_I2V_STEPS", [("upload", 100, fake_step)])
    result = avatar_talk.generate_seedance_i2v_task.apply(
        args=[{"tenant_id": auth_context["tenant_id"], "video_task_id": task_id}],
        task_id=task_id,
    ).get()

    assert result == {"task_id": task_id, "status": "done"}
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
        operation = db.query(BillingOperation).filter_by(result_id=task_id).one()
        usage = db.query(UsageRecord).filter_by(billing_operation_id=operation.id).one()
        assert task.status == "done"
        assert task.params["billing_actual_seconds"] == "6.000"
        assert operation.completion_kind == "succeeded"
        assert operation.requested_credits == Decimal("1000")
        assert operation.settled_credits == Decimal("600")
        assert operation.released_credits == Decimal("400")
        assert usage.status == "settled"
        assert usage.quantity == Decimal("6")
        assert usage.provider == "apimart"
        assert usage.cost_cents == 321
