from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import SecretStr
from test_avatar_talk_worker import (
    _RecordingStore,
    _seed_billed_avatar_task,
    _seed_reserved_task,
)
from test_avatar_talk_worker import (
    worker_db as _worker_db,
)

from app.db.models import Asset, AvatarProviderRun, Subscription, TaskAsset, UsageRecord, VideoTask
from app.providers.avatar.heygen import HeyGenPending
from app.services.avatar_runs import create_avatar_run
from app.workers import avatar_talk
from app.workers.heygen_avatar import heygen_step as real_heygen_step

worker_db = _worker_db


class MemoryStorage:
    bucket = "fixture"

    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, content, *, content_type):
        self.objects[key] = content
        return key

    def get_bytes(self, key):
        return self.objects[key]

    def presign_get_url(self, key, **kwargs):
        return f"https://media.example/{key}"


@pytest.fixture
def setup_worker(worker_db, monkeypatch):
    from app.workers import heygen_avatar

    tenant, task_id = _seed_reserved_task(worker_db)
    with worker_db() as db:
        task = db.get(VideoTask, task_id)
        task.params = {"avatar_provider": "heygen", "avatar_model": "avatar_iv"}
        create_avatar_run(db, task=task)
        asset = Asset(
            tenant_id=tenant,
            type="avatar_image",
            source="upload",
            status="ready",
            storage_key=f"tenants/{tenant}/uploads/photo.png",
        )
        db.add(asset)
        db.flush()
        db.add(TaskAsset(video_task_id=task_id, asset_id=asset.id, role="input_avatar"))
        db.commit()
    store, storage = _RecordingStore(), MemoryStorage()
    store.read = lambda _key: store.events[-1] if store.events else None
    monkeypatch.setattr(avatar_talk, "SessionLocal", worker_db)
    monkeypatch.setattr(avatar_talk, "build_progress_store", lambda _: store)
    monkeypatch.setattr(avatar_talk, "create_object_storage", lambda _: storage)
    monkeypatch.setattr(avatar_talk.settings, "heygen_api_key", SecretStr("fixture-only"))
    monkeypatch.setattr(
        avatar_talk.settings, "engine_heygen_avatar_iv_cny_per_second", Decimal(".35")
    )
    monkeypatch.setattr(avatar_talk, "script_step", lambda ctx: ctx)
    counts = {"tts": 0, "avatar": 0}

    def tts(ctx):
        counts["tts"] += 1
        ctx.audio_key = f"tenants/{tenant}/videos/{task_id}/audio.mp3"
        storage.objects[ctx.audio_key] = b"tts"
        ctx.duration_sec = 8
        ctx.timeline = [{"text": "script", "start_ms": 0, "end_ms": 8000}]
        return ctx

    def avatar(ctx, **kwargs):
        counts["avatar"] += 1
        raise HeyGenPending("Synthetic uncertain remote response")

    monkeypatch.setattr(avatar_talk, "tts_step", tts)
    monkeypatch.setattr(heygen_avatar, "heygen_step", avatar)
    return tenant, task_id, store, storage, counts


def _entry(tenant, task):
    return avatar_talk.generate_avatar_talk_task.apply(
        args=[{"tenant_id": tenant, "video_task_id": task}], task_id=task, throw=True
    ).get()


def test_real_entry_pending_holds_reservation_and_resume_skips_tts(worker_db, setup_worker):
    tenant, task, store, _, counts = setup_worker
    assert _entry(tenant, task)["status"] == "running"
    assert _entry(tenant, task)["status"] == "running"
    assert counts == {"tts": 1, "avatar": 2}
    with worker_db() as db:
        video = db.get(VideoTask, task)
        run = db.get(AvatarProviderRun, task)
        assert video.status == "running" and video.error_code == "HEYGEN_PENDING"
        assert run.checkpoint["audio_key"].startswith(f"tenants/{tenant}/")
        assert db.get(Subscription, "sub-avatar").quota_credits_reserved == 12
        assert db.query(UsageRecord).filter_by(video_task_id=task).one().status == "reserved"
    assert store.events[-1]["error_code"] == "HEYGEN_PENDING"


def test_uncertain_tts_checkpoint_requires_review_without_second_paid_call(worker_db, setup_worker):
    tenant, task, _, _, counts = setup_worker
    with worker_db() as db:
        run = db.get(AvatarProviderRun, task)
        run.checkpoint = {"tts_started": True}
        db.commit()
    assert _entry(tenant, task)["status"] == "running"
    assert counts == {"tts": 0, "avatar": 0}
    with worker_db() as db:
        assert db.get(VideoTask, task).error_code == "HEYGEN_REVIEW_REQUIRED"
        assert db.get(Subscription, "sub-avatar").quota_credits_reserved == 12


def test_missing_heygen_cost_fails_before_tts(worker_db, setup_worker, monkeypatch):
    tenant, task, _, _, counts = setup_worker
    monkeypatch.setattr(avatar_talk.settings, "engine_heygen_avatar_iv_cny_per_second", None)
    assert _entry(tenant, task)["status"] == "failed"
    assert counts == {"tts": 0, "avatar": 0}
    with worker_db() as db:
        assert db.get(VideoTask, task).error_code == "HEYGEN_COST_NOT_CONFIGURED"
        assert db.get(Subscription, "sub-avatar").quota_credits_reserved == 0


def test_completed_recovery_settles_once_at_frozen_cost(worker_db, setup_worker, monkeypatch):
    from app.workers import heygen_avatar

    tenant, task, _, storage, counts = setup_worker
    assert _entry(tenant, task)["status"] == "running"
    monkeypatch.setattr(
        avatar_talk.settings, "engine_heygen_avatar_iv_cny_per_second", Decimal("999")
    )

    def avatar(ctx, **kwargs):
        counts["avatar"] += 1
        ctx.base_video_bytes = b"base"
        ctx.db.get(AvatarProviderRun, task).provider_job_id = "accounting-job"
        return ctx

    def upload(ctx):
        ctx.storage_key = f"tenants/{tenant}/videos/{task}/final.mp4"
        ctx.size_bytes = 5
        storage.objects[ctx.storage_key] = b"final"
        return ctx

    monkeypatch.setattr(heygen_avatar, "heygen_step", avatar)
    monkeypatch.setattr(avatar_talk, "subtitle_step", lambda ctx: ctx)
    monkeypatch.setattr(avatar_talk, "compose_step", lambda ctx: ctx)
    monkeypatch.setattr(avatar_talk, "upload_step", upload)
    assert _entry(tenant, task)["status"] == "done"
    assert _entry(tenant, task)["status"] == "done"
    assert counts == {"tts": 1, "avatar": 2}
    with worker_db() as db:
        usage = db.query(UsageRecord).filter_by(video_task_id=task).one()
        assert usage.status == "settled" and usage.cost_cents == 280
        assert usage.provider == "heygen" and usage.model == "avatar_iv"
        assert usage.provider_usage["source"] == "configured_estimate"
        assert usage.provider_cost_usd is None
        sub = db.get(Subscription, "sub-avatar")
        assert sub.quota_credits_reserved == 0 and sub.quota_credits_used == 10
        assert db.get(AvatarProviderRun, task).state == "completed"
        assert usage.provider_usage["provider_job_id"] == "accounting-job"
        usage_id = usage.id
    from app.services.history import delete_video_task

    with worker_db() as db:
        assert delete_video_task(db, tenant_id=tenant, task_id=task, storage=storage) == "deleted"
        usage = db.get(UsageRecord, usage_id)
        assert usage.video_task_id is None
        assert usage.provider_usage["provider_job_id"] == "accounting-job"
        assert usage.cost_cents == 280


def test_due_run_recovery_enqueues_same_task_and_review_is_not_replayed(
    worker_db,
    setup_worker,
    monkeypatch,
):
    from app.workers.heygen_avatar import enqueue_due_avatar_runs

    tenant, task, _, _, _ = setup_worker
    assert _entry(tenant, task)["status"] == "running"
    calls = []
    monkeypatch.setattr(
        avatar_talk.generate_avatar_talk_task, "apply_async", lambda **kwargs: calls.append(kwargs)
    )
    assert enqueue_due_avatar_runs(worker_db, now=datetime.now(UTC) + timedelta(hours=1)) == 1
    assert calls == [
        {"args": [{"tenant_id": tenant, "video_task_id": task}], "task_id": task, "queue": "avatar"}
    ]
    with worker_db() as db:
        db.get(AvatarProviderRun, task).state = "review"
        db.commit()
    assert enqueue_due_avatar_runs(worker_db, now=datetime.now(UTC) + timedelta(days=1)) == 0


def test_terminal_provider_failure_releases_once(worker_db, setup_worker, monkeypatch):
    from app.providers.avatar.heygen import HeyGenTerminalFailure
    from app.workers import heygen_avatar

    tenant, task, _, _, counts = setup_worker

    def failed(ctx, **kwargs):
        counts["avatar"] += 1
        raise HeyGenTerminalFailure("Confirmed provider failure")

    monkeypatch.setattr(heygen_avatar, "heygen_step", failed)
    assert _entry(tenant, task)["status"] == "failed"
    assert _entry(tenant, task)["status"] == "failed"
    assert counts == {"tts": 1, "avatar": 1}
    with worker_db() as db:
        sub = db.get(Subscription, "sub-avatar")
        assert sub.quota_credits_reserved == 0 and sub.quota_credits_used == 0


def test_real_recovery_after_old_timeout_holds_billing_and_enqueues_same_job(
    worker_db, monkeypatch
):
    from app.services.task_recovery import recover_orphaned_image_queue_tasks

    tenant, task_id = _seed_billed_avatar_task(
        worker_db, task_id="heygen-billed", reserved_seconds=10
    )
    old = datetime.now(UTC) - timedelta(hours=2)
    with worker_db() as db:
        task = db.get(VideoTask, task_id)
        task.params = {**task.params, "avatar_provider": "heygen", "avatar_model": "avatar_iv"}
        task.status = "running"
        task.updated_at = old
        run = create_avatar_run(db, task=task)
        run.state, run.provider_job_id, run.next_check_at = "pending", "known-job", old
        db.commit()
        reserved = db.get(Subscription, f"sub-{task_id}").quota_credits_reserved
    calls = []
    monkeypatch.setattr(
        avatar_talk.generate_avatar_talk_task, "apply_async", lambda **kwargs: calls.append(kwargs)
    )
    result = recover_orphaned_image_queue_tasks(session_factory=worker_db, now=datetime.now(UTC))
    assert result.billing_operations_released == 0
    assert calls[0]["task_id"] == task_id and calls[0]["queue"] == "avatar"
    with worker_db() as db:
        assert db.get(VideoTask, task_id).status == "running"
        assert db.get(Subscription, f"sub-{task_id}").quota_credits_reserved == reserved


def test_real_worker_remote_id_checkpoint_recovery_is_get_only(
    worker_db, setup_worker, monkeypatch
):
    import httpx

    from app.db.models import Asset, TaskAsset
    from app.providers.avatar.heygen import HeyGenAvatarProvider
    from app.workers import heygen_avatar

    tenant, task, _, storage, counts = setup_worker
    with worker_db() as db:
        asset = Asset(
            tenant_id=tenant,
            type="avatar_image",
            source="upload",
            status="ready",
            storage_key=f"tenants/{tenant}/uploads/photo.png",
        )
        db.add(asset)
        db.flush()
        db.add(TaskAsset(video_task_id=task, asset_id=asset.id, role="input_avatar"))
        db.commit()
    methods = []

    def transport(request):
        methods.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"video_id": "saved-job"}})
        raise httpx.ReadTimeout("synthetic polling disconnect")

    provider = HeyGenAvatarProvider(
        api_key="fixture-only",
        input_hosts={"media.example"},
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    monkeypatch.setattr("app.providers.url_guard._ensure_public_host", lambda *_args: None)
    monkeypatch.setattr(heygen_avatar, "heygen_factory", lambda: provider)
    monkeypatch.setattr(heygen_avatar, "heygen_step", real_heygen_step)
    assert _entry(tenant, task)["status"] == "running"
    with worker_db() as db:
        assert db.get(AvatarProviderRun, task).provider_job_id == "saved-job"
    assert _entry(tenant, task)["status"] == "running"
    assert methods == ["POST", "GET", "GET"]
    assert counts["tts"] == 1


def test_per_lease_artifact_paths_do_not_overlap():
    from pathlib import Path

    a = avatar_talk.AvatarTalkContext(
        task_id="same-task",
        tenant_id="tenant",
        db=None,
        store=None,
        storage=None,
        work_dir=Path("owner-a"),
        artifact_prefix="tenants/tenant/a",
    )
    b = avatar_talk.AvatarTalkContext(
        task_id="same-task",
        tenant_id="tenant",
        db=None,
        store=None,
        storage=None,
        work_dir=Path("owner-b"),
        artifact_prefix="tenants/tenant/b",
    )
    assert a.work_dir != b.work_dir
    for file in ("audio.mp3", "final.mp4", "subtitle.srt"):
        assert avatar_talk._artifact_key(a, file) != avatar_talk._artifact_key(b, file)


def test_dirty_cross_tenant_source_fails_before_paid_tts(worker_db, setup_worker):
    tenant, task, _, _, counts = setup_worker
    with worker_db() as db:
        asset = db.query(Asset).filter_by(tenant_id=tenant).one()
        asset.storage_key = "tenants/other/uploads/photo.png"
        db.commit()
    assert _entry(tenant, task)["status"] == "failed"
    assert counts["tts"] == 0


def test_shared_avatar_seed_respects_enabled_foreign_keys(auth_db):
    from sqlalchemy import text

    from app.db.models import Plan

    with auth_db() as db:
        assert db.scalar(text("PRAGMA foreign_keys")) == 1
        db.add(
            Plan(
                id="plan-missing-ok-for-sqlite",
                code="fk-fixture",
                name="FK fixture",
                price_cents=0,
                period="monthly",
                quota_credits=100,
                max_concurrent=1,
                seat_limit=1,
            )
        )
        db.commit()
    tenant_id, task_id = _seed_reserved_task(auth_db)
    with auth_db() as db:
        assert db.get(VideoTask, task_id).tenant_id == tenant_id
        subscription = db.get(Subscription, "sub-avatar")
        assert subscription.tenant_id == tenant_id
        usage = db.query(UsageRecord).filter_by(video_task_id=task_id).one()
        assert usage.subscription_id == subscription.id
        assert usage.status == "reserved"
