from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Subscription, Tenant, UsageRecord, User, VideoTask
from app.workers import avatar_talk


class _RecordingStore:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def update(self, task_id: str, **fields) -> None:
        self.events.append({"task_id": task_id, **fields})


class _Storage:
    bucket = "bucket"

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
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


@pytest.fixture
def worker_db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionTesting = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        yield SessionTesting
    finally:
        Base.metadata.drop_all(engine)


def _seed_reserved_task(SessionTesting, *, task_id: str = "avatar-task") -> tuple[str, str]:
    tenant_id = "tenant-avatar"
    now = datetime.now(UTC)
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="avatar", name="Avatar Tenant"))
        db.add(
            User(
                id="user-avatar",
                tenant_id=tenant_id,
                email="owner@example.com",
                password_hash="hash",
            )
        )
        task = VideoTask(
            id=task_id,
            tenant_id=tenant_id,
            created_by_user_id="user-avatar",
            mode="avatar_talk",
            video_mode="avatar_talk",
            status="queued",
            progress=0,
            topic="topic",
            script="script",
        )
        sub = Subscription(
            id="sub-avatar",
            tenant_id=tenant_id,
            plan_id="plan-missing-ok-for-sqlite",
            status="active",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=30),
            quota_credits_total=100,
            quota_credits_used=0,
            quota_credits_reserved=12,
        )
        record = UsageRecord(
            tenant_id=tenant_id,
            subscription_id=sub.id,
            video_task_id=task_id,
            capability="avatar",
            provider="omnihuman",
            model="m",
            unit="second",
            quantity=Decimal("10"),
            credits=Decimal("12.00"),
            cost_cents=0,
            status="reserved",
        )
        db.add_all([task, sub, record])
        db.commit()
    return tenant_id, task_id


def _seed_reserved_seedance_task(
    SessionTesting,
    *,
    task_id: str = "seedance-task",
) -> tuple[str, str]:
    tenant_id = "tenant-seedance"
    now = datetime.now(UTC)
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="seedance", name="Seedance Tenant"))
        db.add(
            User(
                id="user-seedance",
                tenant_id=tenant_id,
                email="seedance-owner@example.com",
                password_hash="hash",
            )
        )
        task = VideoTask(
            id=task_id,
            tenant_id=tenant_id,
            created_by_user_id="user-seedance",
            mode="seedance_i2v",
            video_mode="seedance_i2v",
            status="queued",
            progress=0,
            topic="product",
            script="script",
        )
        sub = Subscription(
            id="sub-seedance",
            tenant_id=tenant_id,
            plan_id="plan-missing-ok-for-sqlite",
            status="active",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=30),
            quota_credits_total=100,
            quota_credits_used=0,
            quota_credits_reserved=20,
        )
        record = UsageRecord(
            tenant_id=tenant_id,
            subscription_id=sub.id,
            video_task_id=task_id,
            capability="video",
            provider="apimart",
            model="doubao-seedance-2.0",
            unit="second",
            quantity=Decimal("10"),
            credits=Decimal("20.00"),
            cost_cents=0,
            status="reserved",
        )
        db.add_all([task, sub, record])
        db.commit()
    return tenant_id, task_id


def _seed_terminal_history(
    SessionTesting,
    *,
    tenant_id: str,
    mode: str,
    prefix: str,
    count: int = 20,
) -> list[str]:
    storage_keys: list[str] = []
    with SessionTesting() as db:
        for index in range(count):
            storage_key = f"tenants/{tenant_id}/videos/{prefix}-{index:02d}/final.mp4"
            storage_keys.append(storage_key)
            db.add(
                VideoTask(
                    id=f"{prefix}-{index:02d}",
                    tenant_id=tenant_id,
                    mode=mode,
                    video_mode=mode,
                    status="done",
                    progress=100,
                    storage_key=storage_key,
                    created_at=datetime(2026, 1, 1, tzinfo=UTC)
                    + timedelta(minutes=index),
                )
            )
        db.commit()
    return storage_keys


def _terminal_history_ids(SessionTesting, *, mode: str) -> set[str]:
    with SessionTesting() as db:
        return {
            task.id
            for task in db.query(VideoTask)
            .filter(VideoTask.mode == mode, VideoTask.status.in_(["done", "failed"]))
            .all()
        }


def test_avatar_talk_task_success_marks_done_and_settles_quota(monkeypatch, worker_db):
    tenant_id, task_id = _seed_reserved_task(worker_db)
    store = _RecordingStore()
    storage = _Storage()
    old_storage_keys = _seed_terminal_history(
        worker_db,
        tenant_id=tenant_id,
        mode="avatar_talk",
        prefix="avatar-history-success",
    )

    def fake_step(ctx):
        ctx.duration_sec = 4
        ctx.storage_key = f"tenants/{tenant_id}/videos/{task_id}/final.mp4"
        ctx.size_bytes = 123
        return ctx

    monkeypatch.setattr(avatar_talk, "SessionLocal", worker_db)
    monkeypatch.setattr(avatar_talk, "build_progress_store", lambda _url: store)
    monkeypatch.setattr(avatar_talk, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(avatar_talk, "AVATAR_TALK_STEPS", [("upload", 100, fake_step)])
    monkeypatch.setattr(
        avatar_talk.provider_costs.settings,
        "engine_omnihuman_cny_per_sec",
        Decimal("1.5"),
        raising=False,
    )

    result = avatar_talk.generate_avatar_talk_task.apply(
        args=[{"tenant_id": tenant_id, "video_task_id": task_id}],
        task_id=task_id,
    ).get()

    assert result["status"] == "done"
    with worker_db() as db:
        task = db.get(VideoTask, task_id)
        sub = db.get(Subscription, "sub-avatar")
        usage = db.query(UsageRecord).filter_by(video_task_id=task_id).one()
        assert task.status == "done"
        assert task.progress == 100
        assert task.storage_key.endswith("/final.mp4")
        assert sub.quota_credits_reserved == 0
        assert sub.quota_credits_used == 5
        assert usage.status == "settled"
        assert usage.cost_cents == 600
    history_ids = _terminal_history_ids(worker_db, mode="avatar_talk")
    assert len(history_ids) == 20
    assert "avatar-history-success-00" not in history_ids
    assert task_id in history_ids
    assert storage.deleted == [old_storage_keys[0]]
    assert store.events[-1]["status"] == "done"
    assert store.events[-1]["progress"] == 100


def test_avatar_talk_task_failure_marks_failed_and_releases_quota(monkeypatch, worker_db):
    tenant_id, task_id = _seed_reserved_task(worker_db, task_id="avatar-fail")
    store = _RecordingStore()
    storage = _Storage()
    old_storage_keys = _seed_terminal_history(
        worker_db,
        tenant_id=tenant_id,
        mode="avatar_talk",
        prefix="avatar-history-failure",
    )

    def fail_step(ctx):
        raise RuntimeError("omnihuman timeout")

    monkeypatch.setattr(avatar_talk, "SessionLocal", worker_db)
    monkeypatch.setattr(avatar_talk, "build_progress_store", lambda _url: store)
    monkeypatch.setattr(avatar_talk, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(avatar_talk, "AVATAR_TALK_STEPS", [("avatar", 80, fail_step)])

    with pytest.raises(RuntimeError, match="omnihuman timeout"):
        avatar_talk.generate_avatar_talk_task.apply(
            args=[{"tenant_id": tenant_id, "video_task_id": task_id}],
            task_id=task_id,
        ).get(propagate=True)

    with worker_db() as db:
        task = db.get(VideoTask, task_id)
        sub = db.get(Subscription, "sub-avatar")
        usage = db.query(UsageRecord).filter_by(video_task_id=task_id).one()
        assert task.status == "failed"
        assert task.error_code == "AVATAR_TALK_FAILED"
        assert "omnihuman timeout" in task.error_message
        assert sub.quota_credits_reserved == 0
        assert sub.quota_credits_used == 0
        assert usage.status == "released"
    history_ids = _terminal_history_ids(worker_db, mode="avatar_talk")
    assert len(history_ids) == 20
    assert "avatar-history-failure-00" not in history_ids
    assert task_id in history_ids
    assert storage.deleted == [old_storage_keys[0]]
    assert store.events[-1]["status"] == "failed"
    assert store.events[-1]["error_code"] == "AVATAR_TALK_FAILED"


def test_seedance_i2v_task_success_marks_done_and_settles_quota(monkeypatch, worker_db):
    tenant_id, task_id = _seed_reserved_seedance_task(worker_db)
    store = _RecordingStore()
    storage = _Storage()
    old_storage_keys = _seed_terminal_history(
        worker_db,
        tenant_id=tenant_id,
        mode="seedance_i2v",
        prefix="seedance-history-success",
    )

    def fake_step(ctx):
        ctx.duration_sec = 5
        ctx.seedance_billable_seconds = 5
        ctx.provider_cost_cents = 511
        ctx.storage_key = f"tenants/{tenant_id}/videos/{task_id}/final.mp4"
        ctx.size_bytes = 456
        return ctx

    monkeypatch.setattr(avatar_talk, "SessionLocal", worker_db)
    monkeypatch.setattr(avatar_talk, "build_progress_store", lambda _url: store)
    monkeypatch.setattr(avatar_talk, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(avatar_talk, "ECOM_I2V_STEPS", [("upload", 100, fake_step)])

    result = avatar_talk.generate_seedance_i2v_task.apply(
        args=[{"tenant_id": tenant_id, "video_task_id": task_id}],
        task_id=task_id,
    ).get()

    assert result["status"] == "done"
    with worker_db() as db:
        task = db.get(VideoTask, task_id)
        sub = db.get(Subscription, "sub-seedance")
        usage = db.query(UsageRecord).filter_by(video_task_id=task_id).one()
        assert task.status == "done"
        assert task.progress == 100
        assert task.storage_key.endswith("/final.mp4")
        assert sub.quota_credits_reserved == 0
        assert sub.quota_credits_used == 10
        assert usage.status == "settled"
        assert usage.provider == "apimart"
        assert usage.model == "doubao-seedance-2.0"
        assert usage.cost_cents == 511
    history_ids = _terminal_history_ids(worker_db, mode="seedance_i2v")
    assert len(history_ids) == 20
    assert "seedance-history-success-00" not in history_ids
    assert task_id in history_ids
    assert storage.deleted == [old_storage_keys[0]]
    assert store.events[-1]["status"] == "done"
    assert store.events[-1]["progress"] == 100


def test_seedance_i2v_task_failure_marks_failed_and_releases_quota(monkeypatch, worker_db):
    tenant_id, task_id = _seed_reserved_seedance_task(worker_db, task_id="seedance-fail")
    store = _RecordingStore()
    storage = _Storage()
    old_storage_keys = _seed_terminal_history(
        worker_db,
        tenant_id=tenant_id,
        mode="seedance_i2v",
        prefix="seedance-history-failure",
    )

    def fail_step(ctx):
        raise RuntimeError("seedance timeout")

    monkeypatch.setattr(avatar_talk, "SessionLocal", worker_db)
    monkeypatch.setattr(avatar_talk, "build_progress_store", lambda _url: store)
    monkeypatch.setattr(avatar_talk, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(avatar_talk, "ECOM_I2V_STEPS", [("seedance", 80, fail_step)])

    with pytest.raises(RuntimeError, match="seedance timeout"):
        avatar_talk.generate_seedance_i2v_task.apply(
            args=[{"tenant_id": tenant_id, "video_task_id": task_id}],
            task_id=task_id,
        ).get(propagate=True)

    with worker_db() as db:
        task = db.get(VideoTask, task_id)
        sub = db.get(Subscription, "sub-seedance")
        usage = db.query(UsageRecord).filter_by(video_task_id=task_id).one()
        assert task.status == "failed"
        assert task.error_code == "SEEDANCE_I2V_FAILED"
        assert "seedance timeout" in task.error_message
        assert sub.quota_credits_reserved == 0
        assert sub.quota_credits_used == 0
        assert usage.status == "released"
    history_ids = _terminal_history_ids(worker_db, mode="seedance_i2v")
    assert len(history_ids) == 20
    assert "seedance-history-failure-00" not in history_ids
    assert task_id in history_ids
    assert storage.deleted == [old_storage_keys[0]]
    assert store.events[-1]["status"] == "failed"
    assert store.events[-1]["error_code"] == "SEEDANCE_I2V_FAILED"
