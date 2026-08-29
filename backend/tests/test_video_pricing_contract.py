from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.exceptions import AppError
from app.db.models import Asset, BillingOperation, BrandVoice, Plan, UsageRecord, User, VideoTask
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
        name="Doubao",
        provider="doubao-voice-clone",
        speaker_id="doubao-speaker",
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
            "script": "你好，世界！",
        }
    )

    estimate = build_video_estimate(db_session, user=user, payload=payload)

    assert isinstance(estimate, BillingQuote)
    assert estimate.pricing_shape == "simple"
    assert estimate.operation == "video_create"
    assert estimate.breakdown == []
    assert estimate.unit == "second"


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
        ("video", "second"),
        ("tts", "character"),
    ]

    with auth_db() as db:
        db.delete(db.get(BrandVoice, voice_id))
        db.commit()
    replay = client.post("/api/v1/videos", headers=submission_headers, json=payload)
    assert replay.status_code == 202, replay.text
    assert replay.json()["data"] == response.json()["data"]
