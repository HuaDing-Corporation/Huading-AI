from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.exceptions import AppError
from app.schemas.videos import VideoGenerateRequest
from app.services import avatar_duration_policy as policy
from app.services.video_pricing import video_pricing_request_hash


def _binding():
    return dict(
        tenant_id="tenant-a",
        user_id="user-a",
        request_hash="a" * 64,
        model="avatar_iv",
        pricing_contract="legacy_estimate",
        accepted_credits=123,
        quote_token=None,
    )


def test_policy_acceptance_is_explicit_and_signed():
    offer = policy.issue_policy(**_binding())
    with pytest.raises(AppError) as error:
        policy.verify_acceptance(version=None, token=offer.token, **_binding())
    assert error.value.code == "AVATAR_DURATION_POLICY_REQUIRED"
    receipt = policy.verify_acceptance(version=offer.version, token=offer.token, **_binding())
    assert receipt["accepted_credits"] == 123
    assert receipt["policy_version"] == "145s-no-refund-v1"


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "tenant-b"),
        ("user_id", "user-b"),
        ("model", "lipsync_precision"),
        ("request_hash", "b" * 64),
        ("accepted_credits", 124),
        ("quote_token", "different-quote"),
        ("pricing_contract", "billing_quote"),
    ],
)
def test_policy_rejects_changed_binding(field, value):
    offer = policy.issue_policy(**_binding())
    with pytest.raises(AppError) as error:
        policy.verify_acceptance(
            version=offer.version, token=offer.token, **{**_binding(), field: value}
        )
    assert error.value.code == "AVATAR_DURATION_POLICY_INVALID"


def test_policy_expiry_zero_amount_and_worker_receipt():
    now = datetime.now(UTC).replace(microsecond=0)
    binding = {**_binding(), "accepted_credits": 0}
    offer = policy.issue_policy(**binding, now=now)
    receipt = policy.verify_acceptance(version=offer.version, token=offer.token, **binding, now=now)
    assert policy.validate_receipt(receipt, tenant_id="tenant-a", user_id="user-a") == 0
    receipt["accepted_credits"] = 100
    with pytest.raises(AppError):
        policy.validate_receipt(receipt, tenant_id="tenant-a", user_id="user-a")
    with pytest.raises(AppError):
        policy.verify_acceptance(
            version=offer.version, token=offer.token, **binding, now=now + timedelta(minutes=11)
        )


def test_optional_policy_fields_do_not_change_legacy_request_hash():
    original = VideoGenerateRequest(
        topic="speech", video_mode="avatar_talk", avatar_asset_id="a", voice_id="voice"
    )
    accepted = original.model_copy(
        update={
            "avatar_duration_policy": "145s-no-refund-v1",
            "avatar_duration_policy_token": "opaque",
        }
    )
    # Computed from the pre-policy VideoGenerateRequest at base 7955fbb8.
    assert video_pricing_request_hash(original) == (
        "1ce4953d4c5ab53a28f93942737dd9f28de3cb08b01be237e54108eadb4063a4"
    )
    assert video_pricing_request_hash(original) == video_pricing_request_hash(accepted)


def test_accepted_receipt_survives_worker_queue_delay():
    accepted_at = datetime.now(UTC) - timedelta(days=1)
    offer = policy.issue_policy(**_binding(), now=accepted_at)
    receipt = policy.verify_acceptance(
        version=offer.version,
        token=offer.token,
        **_binding(),
        now=accepted_at,
    )
    with pytest.raises(AppError):
        policy.verify_acceptance(version=offer.version, token=offer.token, **_binding())
    assert policy.validate_receipt(receipt, tenant_id="tenant-a", user_id="user-a") == 123


def test_existing_billed_order_replays_without_retrospective_acceptance(db_session):
    from uuid import uuid4

    from starlette.requests import Request
    from test_heygen_worker import MemoryStorage
    from test_video_pricing_contract import _brand_i2v_payload

    from app.api.deps import BillingSubmissionHeaders
    from app.api.v1.routes.videos import _create_billing_quote_video, create_video
    from app.db.models import Asset, Subscription, UsageRecord, User
    from app.services.video_pricing import build_video_estimate, resolve_video_pricing_context

    db = db_session
    user = db.get(User, "user-a")
    db.query(Subscription).filter_by(tenant_id=user.tenant_id).one().quota_credits_total = 10000
    payload = _brand_i2v_payload(db, user, "cosyvoice-voice-clone", "720p")
    asset = Asset(
        tenant_id=user.tenant_id,
        type="avatar_image",
        source="upload",
        status="ready",
        storage_key=f"tenants/{user.tenant_id}/uploads/avatar.png",
        mime_type="image/png",
    )
    db.add(asset)
    db.commit()
    payload = payload.model_copy(update={"video_mode": "avatar_talk", "avatar_asset_id": asset.id})
    quote = build_video_estimate(db, user=user, payload=payload)
    headers = BillingSubmissionHeaders(idempotency_key=uuid4(), quote_token=quote.quote_token)
    task, operation, _ = _create_billing_quote_video(
        payload,
        context=resolve_video_pricing_context(db, user=user, payload=payload),
        headers=headers,
        user=user,
        db=db,
        storage=MemoryStorage(),
        submitted_at=datetime.now(UTC),
    )
    assert operation.duration_policy_version is None
    count = db.query(UsageRecord).count()
    reserved = (
        db.query(Subscription).filter_by(tenant_id=user.tenant_id).one().quota_credits_reserved
    )
    for request_payload in (
        payload,
        payload.model_copy(
            update={
                "avatar_duration_policy": policy.VERSION,
                "avatar_duration_policy_token": "expired",
            }
        ),
    ):
        replay = create_video(
            Request({"type": "http"}),
            request_payload,
            user=user,
            db=db,
            storage=MemoryStorage(),
            headers=headers,
        )
        assert replay.data.task_id == task.id
        assert db.query(UsageRecord).count() == count
        assert "avatar_duration_acceptance" not in task.params
        assert operation.duration_policy_version is None
        assert (
            db.query(Subscription).filter_by(tenant_id=user.tenant_id).one().quota_credits_reserved
            == reserved
        )


@pytest.mark.parametrize("seconds,over", [(145, False), (145.0001, True), (145.001, True)])
def test_actual_duration_boundary(seconds, over):
    assert (
        policy.measured_duration(seconds) > Decimal("145")
        if over
        else (policy.measured_duration(seconds) == Decimal("145"))
    )


@pytest.mark.parametrize("seconds", [None, True, 0, -1, float("nan"), float("inf"), "bad"])
def test_untrusted_duration_is_not_a_chargeable_measurement(seconds):
    with pytest.raises(AppError):
        policy.measured_duration(seconds)


@pytest.mark.parametrize("source", ["avatar_asset_id", "avatar_video_asset_id"])
def test_new_avatar_without_consent_rejected_before_reserve(db_session, source):
    from starlette.requests import Request

    from app.api.v1.routes.videos import create_video
    from app.db.models import UsageRecord, User, VideoTask, Voice

    user = db_session.get(User, "user-a")
    voice = Voice(provider="doubao-seed-tts", voice_code="fixture", display_name="Fixture")
    db_session.add(voice)
    db_session.commit()
    payload = VideoGenerateRequest(
        topic="speech",
        script="A prepared speech",
        video_mode="avatar_talk",
        voice_id=voice.id,
        **{source: "source"},
    )
    with pytest.raises(AppError) as error:
        create_video(
            Request({"type": "http"}), payload, user=user, db=db_session, storage=None, headers=None
        )
    assert error.value.code == "AVATAR_DURATION_POLICY_REQUIRED"
    assert db_session.query(VideoTask).count() == 0
    assert db_session.query(UsageRecord).count() == 0


def test_avatar_estimate_issues_policy_without_changing_legacy_price(db_session):
    from app.db.models import User, Voice
    from app.services.quota import estimate_avatar_talk_quota
    from app.services.video_pricing import build_video_estimate

    user = db_session.get(User, "user-a")
    voice = Voice(provider="doubao-seed-tts", voice_code="fixture", display_name="Fixture")
    db_session.add(voice)
    db_session.commit()
    payload = VideoGenerateRequest(
        topic="speech",
        script="A prepared speech",
        video_mode="avatar_talk",
        avatar_asset_id="source",
        voice_id=voice.id,
    )
    offer = build_video_estimate(db_session, user=user, payload=payload)
    expected = estimate_avatar_talk_quota(
        db_session,
        tenant_id=user.tenant_id,
        script=payload.script,
        speed=payload.speed,
    )
    assert offer.estimated_credits == expected.reservation_units
    assert offer.avatar_duration_policy.accepted_credits == expected.reservation_units


@pytest.mark.parametrize(
    "before,after",
    [("0.0001", "1"), ("1", "2"), ("2", "1"), ("0.0001", "0.0001"), ("1", "1")],
)
def test_legacy_reservation_must_equal_accepted_amount_atomically(
    db_session,
    monkeypatch,
    before,
    after,
):
    from sqlalchemy import update
    from starlette.requests import Request
    from test_heygen_worker import MemoryStorage

    from app.api.v1.routes import videos
    from app.db.models import (
        Asset,
        AvatarProviderRun,
        CreditRate,
        Subscription,
        TaskAsset,
        UsageRecord,
        User,
        VideoTask,
        Voice,
    )
    from app.services.video_pricing import build_video_estimate

    db = db_session
    user = db.get(User, "user-a")
    tenant = user.tenant_id
    for capability, unit in (("avatar", "second"), ("tts", "character")):
        db.add(
            CreditRate(
                tenant_id=tenant,
                capability=capability,
                unit=unit,
                credits_per_unit=Decimal(before),
                is_active=True,
            )
        )
    voice = Voice(provider="doubao-seed-tts", voice_code="fixture", display_name="Fixture")
    asset = Asset(
        tenant_id=tenant,
        type="avatar_image",
        source="upload",
        status="ready",
        storage_key=f"tenants/{tenant}/uploads/photo.jpg",
    )
    db.add_all([voice, asset])
    db.commit()
    sub = db.query(Subscription).filter_by(tenant_id=tenant).one()
    initial_wallet = sub.quota_credits_used, sub.quota_credits_reserved
    payload = VideoGenerateRequest(
        topic="probe",
        script="probe",
        video_mode="avatar_talk",
        voice_id=voice.id,
        avatar_asset_id=asset.id,
    )
    offer = build_video_estimate(db, user=user, payload=payload).avatar_duration_policy
    payload = payload.model_copy(
        update={
            "avatar_duration_policy": offer.version,
            "avatar_duration_policy_token": offer.token,
        }
    )
    original_reserve = videos.reserve_avatar_talk_quota

    def change_rate_then_reserve(*args, **kwargs):
        db.execute(
            update(CreditRate)
            .where(CreditRate.tenant_id == tenant)
            .values(
                credits_per_unit=Decimal(after),
            )
        )
        return original_reserve(*args, **kwargs)

    queued = []
    monkeypatch.setattr(videos, "reserve_avatar_talk_quota", change_rate_then_reserve)
    monkeypatch.setattr(
        videos.generate_avatar_talk_task, "apply_async", lambda **kw: queued.append(kw)
    )

    def submit():
        return videos.create_video(
            Request({"type": "http", "headers": []}),
            payload,
            user=user,
            db=db,
            storage=MemoryStorage(),
            headers=None,
        )

    if before != after:
        with pytest.raises(AppError) as error:
            submit()
        assert error.value.code == "AVATAR_DURATION_POLICY_INVALID"
        # The caller has not rolled back: the service must not leave a partial order.
        db.expire_all()
        for model in (VideoTask, AvatarProviderRun, TaskAsset, UsageRecord):
            assert db.query(model).count() == 0
        sub = db.query(Subscription).filter_by(tenant_id=tenant).one()
        assert (sub.quota_credits_used, sub.quota_credits_reserved) == initial_wallet
        assert queued == []
    else:
        submit()
        assert len(queued) == 1
        sub = db.query(Subscription).filter_by(tenant_id=tenant).one()
        assert sub.quota_credits_reserved - initial_wallet[1] == offer.accepted_credits
        assert db.query(VideoTask).count() == db.query(AvatarProviderRun).count() == 1
        for task in db.query(VideoTask):
            videos._video_task_tenants.pop(task.id, None)
