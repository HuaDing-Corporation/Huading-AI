"""Actual PostgreSQL locks and Alembic transitions, no supplier traffic."""

import os
from concurrent.futures import ThreadPoolExecutor
from configparser import ConfigParser
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from test_heygen_duration_settlement import _prepared
from test_heygen_postgres import worker_db as _worker_db
from test_heygen_worker import setup_worker as _setup_worker

from alembic import command
from app.core.config import settings
from app.db.models import BillingOperation, Subscription, Tenant, VideoTask
from app.services.avatar_runs import AvatarLeaseLost
from app.services.heygen_duration_settlement import settle_oversize_audio

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="isolated PostgreSQL required"
)
worker_db = _worker_db
setup_worker = _setup_worker


def test_two_settlers_can_charge_the_same_fence_only_once(worker_db, setup_worker):
    tenant, task_id, _, storage, _ = setup_worker
    owner = _prepared(worker_db, tenant, task_id, storage)
    barrier = Barrier(2)

    def settle():
        with worker_db() as db:
            barrier.wait(timeout=10)
            try:
                result = settle_oversize_audio(
                    db, tenant_id=tenant, task_id=task_id, owner=owner, storage=storage
                )
                db.commit()
                return result
            except AvatarLeaseLost:
                db.rollback()
                return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: settle(), range(2))) == [False, True]
    with worker_db() as db:
        sub = db.get(Subscription, "sub-avatar")
        assert (sub.quota_credits_used, sub.quota_credits_reserved) == (12, 0)


def test_expired_owner_at_financial_flush_cannot_charge(worker_db, setup_worker, monkeypatch):
    from app.services import avatar_runs, quota
    from app.workers.heygen_avatar import _install_write_fence

    tenant, task_id, _, storage, _ = setup_worker
    owner = _prepared(worker_db, tenant, task_id, storage)
    original = quota.settle_locked_subscription_credits

    class ExpiredClock:
        @staticmethod
        def now(tz):
            return datetime.now(UTC) + timedelta(hours=1)

    def expire_before_flush(*args, **kwargs):
        original(*args, **kwargs)
        monkeypatch.setattr(avatar_runs, "datetime", ExpiredClock)

    monkeypatch.setattr(quota, "settle_locked_subscription_credits", expire_before_flush)
    with worker_db() as db:
        fence = _install_write_fence(db, tenant_id=tenant, task_id=task_id, owner=owner)
        try:
            with pytest.raises(AvatarLeaseLost):
                settle_oversize_audio(
                    db, tenant_id=tenant, task_id=task_id, owner=owner, storage=storage
                )
            db.rollback()
        finally:
            event.remove(db, "before_flush", fence)
    with worker_db() as db:
        sub = db.get(Subscription, "sub-avatar")
        assert (sub.quota_credits_used, sub.quota_credits_reserved) == (0, 12)


@pytest.mark.parametrize(
    "changed",
    [
        {"completion_kind": "failed"},
        {"completion_kind": "rejected"},
        {"duration_policy_version": None},
        {"error_code": "GENERIC_FAILURE"},
        {"error_http_status": None},
        {"operation": "script_generate"},
        {"settled_credits": 13},
        {"settled_credits": 11, "released_credits": 1},
    ],
)
def test_postgres_rejects_relaxed_failed_charge_invariants(worker_db, setup_worker, changed):
    tenant, task_id, _, _, _ = setup_worker
    with worker_db() as db:
        operation = BillingOperation(
            tenant_id=tenant,
            user_id="user-avatar",
            operation="video_create",
            idempotency_key=str(uuid4()),
            request_hash="a" * 64,
            quote_hash="b" * 64,
            pricing_snapshot={},
            requested_credits=12,
            settled_credits=12,
            released_credits=0,
            status="completed",
            completion_kind="failed_charged",
            completed_at=datetime.now(UTC),
            duration_policy_version="145s-no-refund-v1",
            error_code="HEYGEN_AUDIO_DURATION_EXCEEDED",
            error_http_status=422,
            error_payload={"detail": None},
            result_type="video_task",
            result_id=task_id,
            result_payload={"task_id": task_id, "status": "failed"},
        )
        db.add(operation)
        db.commit()
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(
                update(BillingOperation)
                .where(BillingOperation.id == operation.id)
                .values(**changed)
            )
        db.expire_all()
        assert db.get(BillingOperation, operation.id).settled_credits == 12


def test_real_migration_upgrade_downgrade_upgrade_and_evidence_guard(monkeypatch):
    url = make_url(os.environ["TEST_POSTGRES_URL"])
    schema = f"duration_migration_{uuid4().hex}"
    root = create_engine(url)
    with root.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(settings, "database_url", isolated.render_as_string(hide_password=False))
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.file_config = ConfigParser(interpolation=None)
    config.file_config.read(config.config_file_name)
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    engine = create_engine(isolated)
    try:
        assert ScriptDirectory.from_config(config).get_heads() == ["20260915_0040"]
        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "20260915_0040"
            )
            checks = {
                row["name"]
                for row in inspect(connection).get_check_constraints("billing_operations")
            }
            assert "ck_billing_operations_failed_charged" in checks
        command.downgrade(config, "20260915_0039")
        command.upgrade(config, "head")
        # An accepted queued legacy task alone is enough to prohibit destructive rollback.
        with engine.begin() as connection:
            connection.execute(
                Tenant.__table__.insert().values(
                    id="guard-tenant",
                    slug="guard-tenant",
                    name="Guard",
                    status="active",
                )
            )
            connection.execute(
                VideoTask.__table__.insert().values(
                    id="guard-video",
                    tenant_id="guard-tenant",
                    mode="avatar_talk",
                    video_mode="avatar_talk",
                    status="queued",
                    progress=0,
                    topic="speech",
                    params={"avatar_duration_acceptance": {"policy_version": "145s-no-refund-v1"}},
                )
            )
        with pytest.raises(RuntimeError, match="Accepted avatar duration policies exist"):
            command.downgrade(config, "20260915_0039")
        with engine.begin() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "20260915_0040"
            )
            assert (
                connection.scalar(text("SELECT count(*) FROM video_tasks WHERE id='guard-video'"))
                == 1
            )
    finally:
        engine.dispose()
        with root.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        root.dispose()
