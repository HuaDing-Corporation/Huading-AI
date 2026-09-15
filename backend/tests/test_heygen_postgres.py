"""Real multi-session gates, enabled by the CI's isolated PostgreSQL URL."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker
from test_heygen_worker import MemoryStorage, _entry, real_heygen_step
from test_heygen_worker import setup_worker as _setup_worker

from app.db.models import Asset, AvatarProviderRun, Plan, Subscription, TaskAsset, VideoTask
from app.providers.avatar.heygen import HeyGenAvatarProvider
from app.services.avatar_runs import claim_avatar_run, create_avatar_run
from app.services.history import clear_video_history, delete_video_task, prune_video_history
from app.workers import heygen_avatar

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="requires isolated PostgreSQL"
)
setup_worker = _setup_worker


@pytest.fixture
def worker_db(cloned_model_metadata_factory):
    url = os.environ["TEST_POSTGRES_URL"]
    schema = f"heygen_test_{uuid4().hex}"
    root = create_engine(url)
    with root.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url,
        connect_args={
            "options": f"-csearch_path={schema} -clock_timeout=5000 -cstatement_timeout=15000",
        },
    )
    try:
        cloned_model_metadata_factory().create_all(engine)
        factory = sessionmaker(bind=engine, autoflush=False)
        with factory() as db:
            db.add(
                Plan(
                    id="plan-missing-ok-for-sqlite",
                    code="fixture",
                    name="Fixture",
                    price_cents=0,
                    period="monthly",
                    quota_credits=100,
                    max_concurrent=1,
                    seat_limit=1,
                )
            )
            db.commit()
        yield factory
    finally:
        engine.dispose()
        with root.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        root.dispose()


def test_postgres_two_claimers_have_one_owner(worker_db, setup_worker):
    tenant, task, _, _, _ = setup_worker
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=10)
        return claim_avatar_run(worker_db, tenant_id=tenant, task_id=task)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sum(value is not None for value in results) == 1


def test_postgres_actual_worker_late_post_response_is_fenced(worker_db, setup_worker, monkeypatch):
    tenant, task, _, _, counts = setup_worker
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
    entered, release = Event(), Event()
    posts = []

    def transport(request):
        if request.method == "POST":
            posts.append((request.headers["Idempotency-Key"], request.content))
            if len(posts) == 1:
                entered.set()
                assert release.wait(20)
                return httpx.Response(200, json={"data": {"video_id": "late-response"}})
            return httpx.Response(200, json={"data": {"video_id": "current-response"}})
        raise httpx.ReadTimeout("Synthetic pending GET")

    def provider():
        return HeyGenAvatarProvider(
            api_key="fixture-only",
            input_hosts={"media.example"},
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
        )

    monkeypatch.setattr("app.providers.url_guard._ensure_public_host", lambda *_args: None)
    monkeypatch.setattr(heygen_avatar, "heygen_factory", provider)
    monkeypatch.setattr(heygen_avatar, "heygen_step", real_heygen_step)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(_entry, tenant, task)
        assert entered.wait(15)
        try:
            with worker_db() as db:
                run = db.get(AvatarProviderRun, task)
                run.lease_until = datetime.now(UTC) - timedelta(seconds=1)
                db.commit()
            assert _entry(tenant, task)["status"] == "running"
        finally:
            release.set()
        assert first.result(timeout=15)["status"] == "running"
    assert len(posts) == 2 and posts[0] == posts[1]
    assert counts["tts"] == 1
    with worker_db() as db:
        assert db.get(AvatarProviderRun, task).provider_job_id == "current-response"
        assert db.get(Subscription, "sub-avatar").quota_credits_reserved == 12
        assert db.get(VideoTask, task).storage_key is None


@pytest.mark.parametrize("operation,remaining", [("delete", 21), ("prune", 21), ("clear", 1)])
def test_postgres_terminal_history_cascades_but_preserves_running(
    worker_db,
    setup_worker,
    operation,
    remaining,
):
    tenant, running_id, _, _, _ = setup_worker
    with worker_db() as db:
        for index in range(21):
            task = VideoTask(
                id=f"completed-{index}",
                tenant_id=tenant,
                status="done",
                mode="avatar_talk",
                video_mode="avatar_talk",
                params={"avatar_provider": "heygen", "avatar_model": "avatar_iv"},
            )
            db.add(task)
            create_avatar_run(db, task=task).state = "completed"
        db.commit()
        storage = MemoryStorage()
        if operation == "delete":
            assert (
                delete_video_task(db, tenant_id=tenant, task_id="completed-0", storage=storage)
                == "deleted"
            )
        elif operation == "prune":
            assert (
                prune_video_history(db, tenant_id=tenant, mode="avatar_talk", storage=storage) == 1
            )
        else:
            assert (
                clear_video_history(db, tenant_id=tenant, mode="avatar_talk", storage=storage) == 21
            )
        assert db.scalar(select(func.count()).select_from(AvatarProviderRun)) == remaining
        assert db.get(AvatarProviderRun, running_id) is not None
