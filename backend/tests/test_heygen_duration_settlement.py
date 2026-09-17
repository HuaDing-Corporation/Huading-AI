import hashlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_heygen_worker import setup_worker as _setup_worker
from test_heygen_worker import worker_db as _worker_db

from app.db.models import AvatarProviderRun, Subscription, UsageRecord, VideoTask
from app.services import avatar_duration_policy as policy
from app.services.avatar_runs import claim_avatar_run
from app.services.heygen_duration_settlement import settle_oversize_audio

setup_worker = _setup_worker
worker_db = _worker_db


def _prepared(factory, tenant, task_id, storage, *, duration=146, accepted=12):
    owner = claim_avatar_run(factory, tenant_id=tenant, task_id=task_id)
    audio_key = f"tenants/{tenant}/videos/{task_id}/audio.mp3"
    storage.objects[audio_key] = b"verified audio fixture"
    with factory() as db:
        task = db.get(VideoTask, task_id)
        binding = dict(
            tenant_id=tenant,
            user_id=task.created_by_user_id,
            request_hash="a" * 64,
            model="avatar_iv",
            pricing_contract="legacy_estimate",
            accepted_credits=accepted,
            quote_token=None,
        )
        offer = policy.issue_policy(**binding)
        receipt = policy.verify_acceptance(version=offer.version, token=offer.token, **binding)
        task.params = {**task.params, "avatar_duration_acceptance": receipt}
        run = db.get(AvatarProviderRun, task_id)
        run.checkpoint = {
            "tts_started": True,
            "audio_key": audio_key,
            "duration_sec": duration,
            "timeline": [],
            "audio_measurement": {
                "source": "ffprobe",
                "seconds": str(duration),
                "audio_key": audio_key,
                "sha256": hashlib.sha256(storage.objects[audio_key]).hexdigest(),
                "complete": True,
            },
        }
        video_usage = db.query(UsageRecord).filter_by(video_task_id=task_id).one()
        video_usage.provider = "heygen"
        video_usage.model = "avatar_iv"
        db.add(
            UsageRecord(
                tenant_id=tenant,
                video_task_id=task_id,
                capability="tts",
                provider="doubao-seed-tts",
                unit="char",
                quantity=6,
                credits=0,
                cost_cents=1,
                status="settled",
                settled_at=datetime.now(UTC),
            )
        )
        db.commit()
    return owner


def test_legacy_positive_rate_rounding_to_zero_settles_and_retains_evidence(
    worker_db,
    setup_worker,
):
    from app.api.v1.routes.videos import _sse_payload, _video_read
    from app.db.models import CreditRate, User, Voice
    from app.schemas.videos import VideoGenerateRequest
    from app.services.history import delete_video_task
    from app.services.quota import reserve_avatar_talk_quota
    from app.services.video_pricing import build_video_estimate

    tenant, task_id, _, storage, _ = setup_worker
    with worker_db() as db:
        db.query(UsageRecord).filter_by(video_task_id=task_id).delete()
        sub = db.get(Subscription, "sub-avatar")
        sub.quota_credits_reserved = 0
        for capability, unit in (("avatar", "second"), ("tts", "character")):
            db.add(
                CreditRate(
                    tenant_id=tenant,
                    capability=capability,
                    unit=unit,
                    credits_per_unit=Decimal("0.0001"),
                    is_active=True,
                )
            )
        voice = Voice(provider="doubao-seed-tts", voice_code="fixture", display_name="Fixture")
        db.add(voice)
        db.commit()
        payload = VideoGenerateRequest(
            topic="speech",
            script="hi",
            video_mode="avatar_talk",
            avatar_asset_id="source",
            voice_id=voice.id,
        )
        estimate = build_video_estimate(db, user=db.get(User, "user-avatar"), payload=payload)
        assert estimate.pricing_contract == "legacy_estimate"
        assert estimate.estimated_credits == estimate.avatar_duration_policy.accepted_credits == 0
        reserve_avatar_talk_quota(
            db,
            tenant_id=tenant,
            video_task_id=task_id,
            script=payload.script,
            speed=payload.speed,
            provider="heygen",
            model="avatar_iv",
        )
        db.commit()
        assert sub.quota_credits_reserved == 0
    owner = _prepared(worker_db, tenant, task_id, storage, accepted=0)
    with worker_db() as db:
        assert settle_oversize_audio(
            db,
            tenant_id=tenant,
            task_id=task_id,
            owner=owner,
            storage=storage,
        )
        db.commit()
        task = db.get(VideoTask, task_id)
        public = _video_read(task, storage=storage).model_dump(mode="json")
        outcome = _sse_payload(task_id, public)["billing_outcome"]
        assert public["status"] == "failed" and task.error_code == policy.ERROR_CODE
        assert outcome["requested_credits"] == outcome["settled_credits"] == 0
        assert outcome["released_credits"] == 0 and outcome["status"] == "settled"
        sub = db.get(Subscription, "sub-avatar")
        assert sub.quota_credits_used == sub.quota_credits_reserved == 0
        assert (
            delete_video_task(db, tenant_id=tenant, task_id=task_id, storage=storage) == "deleted"
        )
        evidence = (
            db.query(UsageRecord).filter_by(capability="avatar").one().duration_failure_evidence
        )
        assert evidence["settled_credits"] == evidence["acceptance"]["accepted_credits"] == 0
        assert evidence["original_task_id"] == task_id
    assert claim_avatar_run(worker_db, tenant_id=tenant, task_id=task_id) is None


def test_charge_is_atomic_bounded_and_does_not_fabricate_provider_cost(worker_db, setup_worker):
    tenant, task_id, _, storage, _ = setup_worker
    owner = _prepared(worker_db, tenant, task_id, storage)
    with worker_db() as db:
        assert settle_oversize_audio(
            db, tenant_id=tenant, task_id=task_id, owner=owner, storage=storage
        )
        db.commit()
    with worker_db() as db:
        task = db.get(VideoTask, task_id)
        assert task.status == "failed" and task.error_code == policy.ERROR_CODE
        assert task.params["billing_outcome"]["settled_credits"] == 12
        sub = db.get(Subscription, "sub-avatar")
        assert (sub.quota_credits_used, sub.quota_credits_reserved) == (12, 0)
        usages = db.query(UsageRecord).filter_by(video_task_id=task_id).all()
        assert {u.capability: u.cost_cents for u in usages} == {"avatar": 0, "tts": 1}
        assert sum(u.credits for u in usages) == Decimal(12)
        assert db.get(AvatarProviderRun, task_id).state == "failed"
    assert claim_avatar_run(worker_db, tenant_id=tenant, task_id=task_id) is None
    from app.services.history import delete_video_task

    with worker_db() as db:
        assert (
            delete_video_task(db, tenant_id=tenant, task_id=task_id, storage=storage) == "deleted"
        )
        evidence = (
            db.query(UsageRecord).filter_by(capability="avatar").one().duration_failure_evidence
        )
        assert evidence["original_task_id"] == task_id
        assert evidence["settled_credits"] == 12
        assert (
            policy.validate_receipt(evidence["acceptance"], tenant_id=tenant, user_id="user-avatar")
            == 12
        )


@pytest.mark.parametrize("accepted", [0, 12])
def test_admin_retry_cannot_reopen_a_settled_duration_failure(worker_db, setup_worker, accepted):
    from app.core.exceptions import AppError
    from app.db.models import User
    from app.services.admin_console import prepare_task_retry

    tenant, task_id, _, storage, _ = setup_worker
    with worker_db() as db:
        db.get(Subscription, "sub-avatar").quota_credits_reserved = accepted
        db.query(UsageRecord).filter_by(video_task_id=task_id).one().credits = accepted
        db.commit()
    owner = _prepared(worker_db, tenant, task_id, storage, accepted=accepted)
    with worker_db() as db:
        assert settle_oversize_audio(
            db, tenant_id=tenant, task_id=task_id, owner=owner, storage=storage
        )
        db.commit()
        with pytest.raises(AppError) as error:
            prepare_task_retry(db, actor=db.get(User, "user-avatar"), task_id=task_id)
        assert error.value.code == "TASK_NOT_RETRYABLE"
        assert db.get(VideoTask, task_id).status == "failed"
        assert db.get(AvatarProviderRun, task_id).state == "failed"
        sub = db.get(Subscription, "sub-avatar")
        assert (sub.quota_credits_used, sub.quota_credits_reserved) == (accepted, 0)


@pytest.mark.parametrize("seconds,charged", [(145, False), (145.001, True)])
def test_exact_duration_boundary(worker_db, setup_worker, seconds, charged):
    tenant, task_id, _, storage, _ = setup_worker
    owner = _prepared(worker_db, tenant, task_id, storage, duration=seconds)
    with worker_db() as db:
        assert (
            settle_oversize_audio(
                db, tenant_id=tenant, task_id=task_id, owner=owner, storage=storage
            )
            is charged
        )
        db.rollback()
    with worker_db() as db:
        assert db.get(Subscription, "sub-avatar").quota_credits_reserved == 12
        assert db.get(Subscription, "sub-avatar").quota_credits_used == 0


@pytest.mark.parametrize("provider", ["cosyvoice-voice-clone", "doubao-voice-clone"])
def test_real_quote_creation_settlement_lookup_and_history_deletion(db_session, provider):
    from uuid import uuid4

    from sqlalchemy.orm import sessionmaker
    from test_heygen_worker import MemoryStorage
    from test_video_pricing_contract import _brand_i2v_payload

    from app.api.deps import BillingSubmissionHeaders
    from app.api.v1.routes.videos import _create_billing_quote_video, _sse_payload, _video_read
    from app.db.models import Asset, BillingOperation, User
    from app.services.billing_operations import lookup_operation
    from app.services.history import delete_video_task
    from app.services.video_pricing import (
        avatar_policy_binding,
        build_video_estimate,
        resolve_video_pricing_context,
    )

    db = db_session
    user = db.get(User, "user-a")
    db.query(Subscription).filter_by(tenant_id=user.tenant_id).one().quota_credits_total = 10000
    payload = _brand_i2v_payload(db, user, provider, "720p")
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
    context = resolve_video_pricing_context(db, user=user, payload=payload)
    quote = build_video_estimate(db, user=user, payload=payload)
    receipt = policy.verify_acceptance(
        version=quote.avatar_duration_policy.version,
        token=quote.avatar_duration_policy.token,
        **avatar_policy_binding(
            db, user=user, payload=payload, context=context, quote_token=quote.quote_token
        ),
    )
    key = uuid4()
    storage = MemoryStorage()
    task, operation, created = _create_billing_quote_video(
        payload,
        context=context,
        headers=BillingSubmissionHeaders(idempotency_key=key, quote_token=quote.quote_token),
        user=user,
        db=db,
        storage=storage,
        submitted_at=datetime.now(UTC),
        duration_acceptance=receipt,
    )
    assert created
    task_id, tenant, operation_id = task.id, user.tenant_id, operation.id
    factory = sessionmaker(bind=db.get_bind(), autoflush=False)
    owner = claim_avatar_run(factory, tenant_id=tenant, task_id=task_id)
    db.expire_all()
    audio_key = f"tenants/{tenant}/videos/{task_id}/audio.mp3"
    storage.objects[audio_key] = b"verified audio"
    run = db.get(AvatarProviderRun, task_id)
    run.checkpoint = {
        "tts_started": True,
        "audio_key": audio_key,
        "duration_sec": 146,
        "audio_measurement": {
            "source": "ffprobe",
            "complete": True,
            "seconds": "146",
            "audio_key": audio_key,
            "sha256": hashlib.sha256(storage.objects[audio_key]).hexdigest(),
        },
    }
    tts = db.query(UsageRecord).filter_by(video_task_id=task_id, capability="tts").one_or_none()
    if tts is None:
        tts = UsageRecord(
            tenant_id=tenant,
            video_task_id=task_id,
            capability="tts",
            provider="doubao-seed-tts",
            unit="char",
            quantity=8,
            credits=0,
            cost_cents=1,
            status="settled",
            settled_at=datetime.now(UTC),
        )
        db.add(tts)
    else:
        tts.cost_cents = 1
        tts.provider_usage = {"characters": 8, "cost_cents": 1}
    db.commit()
    assert settle_oversize_audio(
        db, tenant_id=tenant, task_id=task_id, owner=owner, storage=storage
    )
    db.commit()
    db.expire_all()
    op = db.get(BillingOperation, operation_id)
    assert op.completion_kind == "failed_charged"
    assert op.requested_credits == op.settled_credits == quote.payable_credits
    assert op.released_credits == 0
    public = _video_read(db.get(VideoTask, task_id), storage=storage).model_dump(mode="json")
    assert public["status"] == "failed" and public["download_url"] is None
    assert (
        _sse_payload(task_id, public)["billing_outcome"]["settled_credits"] == quote.payable_credits
    )
    for deleted in (False, True):
        if deleted:
            assert (
                delete_video_task(db, tenant_id=tenant, task_id=task_id, storage=storage)
                == "deleted"
            )
        lookup = lookup_operation(
            db, tenant_id=tenant, user_id=user.id, operation="video_create", idempotency_key=key
        )
        assert lookup.completion_kind == "failed_charged"
        assert lookup.resource.status == "failed" and lookup.resource.task_id == task_id
        assert lookup.result is None and lookup.result_type is None
        assert lookup.billing.settled_credits == quote.payable_credits


@pytest.mark.parametrize(
    "alter",
    [
        "old",
        "post_attempted",
        "job",
        "request_body",
        "base_key",
        "remote_completed",
        "unmeasured",
        "different_audio",
    ],
)
def test_old_or_ambiguous_state_cannot_authorize_charge(worker_db, setup_worker, alter):
    tenant, task_id, _, storage, _ = setup_worker
    owner = _prepared(worker_db, tenant, task_id, storage)
    with worker_db() as db:
        task, run = db.get(VideoTask, task_id), db.get(AvatarProviderRun, task_id)
        if alter == "old":
            task.params = {"avatar_provider": "heygen", "avatar_model": "avatar_iv"}
        elif alter == "job":
            run.provider_job_id = "known-job"
        elif alter == "request_body":
            run.request_body = {"already": "prepared"}
        elif alter == "unmeasured":
            run.checkpoint = {**run.checkpoint, "audio_measurement": None}
        elif alter == "different_audio":
            storage.objects[run.checkpoint["audio_key"]] = b"changed"
        else:
            run.checkpoint = {**run.checkpoint, alter: True}
        db.commit()
    with worker_db() as db:
        assert (
            settle_oversize_audio(
                db, tenant_id=tenant, task_id=task_id, owner=owner, storage=storage
            )
            is False
        )
        assert db.get(Subscription, "sub-avatar").quota_credits_used == 0


@pytest.mark.parametrize("valid_audio", [True, False])
def test_real_tts_meter_drives_worker_and_retains_cost_on_decode_failure(
    worker_db,
    setup_worker,
    monkeypatch,
    tmp_path,
    valid_audio,
):
    from avatar_policy_helpers import write_decodable_audio
    from test_heygen_worker import _entry, real_tts_step

    from app.workers import avatar_talk

    tenant, task_id, _, storage, counts = setup_worker
    with worker_db() as db:
        task = db.get(VideoTask, task_id)
        task.script = "x" * 100
        binding = dict(
            tenant_id=tenant,
            user_id=task.created_by_user_id,
            request_hash="c" * 64,
            model="avatar_iv",
            pricing_contract="legacy_estimate",
            accepted_credits=12,
            quote_token=None,
        )
        offer = policy.issue_policy(**binding)
        task.params = {
            **task.params,
            "avatar_duration_acceptance": policy.verify_acceptance(
                version=offer.version,
                token=offer.token,
                **binding,
            ),
        }
        usage = db.query(UsageRecord).filter_by(video_task_id=task_id).one()
        usage.provider, usage.model = "heygen", "avatar_iv"
        db.commit()
    audio_path = tmp_path / "source.mp3"
    if valid_audio:
        write_decodable_audio(audio_path, seconds=146)
    else:
        audio_path.write_bytes(b"not decodable audio")
    calls = []

    class Provider:
        async def synthesize_speech(self, payload):
            calls.append(payload)
            return {
                "audio_path": str(audio_path),
                "duration_ms": 146000,
                "provider": "doubao-seed-tts",
                "characters": len(payload["text"]),
                "timeline": [],
            }

    monkeypatch.setattr(avatar_talk, "tts_step", real_tts_step)
    monkeypatch.setattr(
        avatar_talk, "_tts_voice_for_task", lambda *a, **k: ("voice", "preset", None)
    )
    monkeypatch.setattr(avatar_talk, "_tts_provider_for_voice", lambda *a, **k: (Provider(), "tts"))
    monkeypatch.setattr(avatar_talk, "_tail_faded_tts_audio", lambda path: path)
    monkeypatch.setattr(
        avatar_talk, "_apply_synthetic_label", lambda ctx, content, **k: (content, {})
    )
    assert _entry(tenant, task_id)["status"] == ("failed" if valid_audio else "running")
    assert _entry(tenant, task_id)["status"] == ("failed" if valid_audio else "running")
    assert len(calls) == 1 and counts["avatar"] == 0
    with worker_db() as db:
        sub = db.get(Subscription, "sub-avatar")
        assert (sub.quota_credits_used, sub.quota_credits_reserved) == (
            (12, 0) if valid_audio else (0, 12)
        )
        tts = db.query(UsageRecord).filter_by(video_task_id=task_id, capability="tts").one()
        assert tts.cost_cents > 0 and tts.credits == 0
        run = db.get(AvatarProviderRun, task_id)
        assert not run.checkpoint.get("post_attempted") and not run.provider_job_id
        if valid_audio:
            assert Decimal(run.checkpoint["audio_measurement"]["seconds"]) > 145
        else:
            assert run.state == "review"
