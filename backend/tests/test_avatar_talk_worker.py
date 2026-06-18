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


def test_avatar_talk_task_success_marks_done_and_settles_quota(monkeypatch, worker_db):
    tenant_id, task_id = _seed_reserved_task(worker_db)
    store = _RecordingStore()

    def fake_step(ctx):
        ctx.duration_sec = 4
        ctx.storage_key = f"tenants/{tenant_id}/videos/{task_id}/final.mp4"
        ctx.size_bytes = 123
        return ctx

    monkeypatch.setattr(avatar_talk, "SessionLocal", worker_db)
    monkeypatch.setattr(avatar_talk, "build_progress_store", lambda _url: store)
    monkeypatch.setattr(avatar_talk, "create_object_storage", lambda _settings: _Storage())
    monkeypatch.setattr(avatar_talk, "AVATAR_TALK_STEPS", [("upload", 100, fake_step)])

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
        assert usage.cost_cents == 400
    assert store.events[-1]["status"] == "done"
    assert store.events[-1]["progress"] == 100


def test_avatar_talk_task_failure_marks_failed_and_releases_quota(monkeypatch, worker_db):
    tenant_id, task_id = _seed_reserved_task(worker_db, task_id="avatar-fail")
    store = _RecordingStore()

    def fail_step(ctx):
        raise RuntimeError("omnihuman timeout")

    monkeypatch.setattr(avatar_talk, "SessionLocal", worker_db)
    monkeypatch.setattr(avatar_talk, "build_progress_store", lambda _url: store)
    monkeypatch.setattr(avatar_talk, "create_object_storage", lambda _settings: _Storage())
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
    assert store.events[-1]["status"] == "failed"
    assert store.events[-1]["error_code"] == "AVATAR_TALK_FAILED"
