from __future__ import annotations

import ast
import os
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import AppError
from app.db.models import (
    Base,
    Plan,
    ReversePromptJob,
    Subscription,
    Tenant,
    UsageRecord,
    User,
    VideoTask,
)
from app.services import admin_console, quota

_RUNTIME_QUOTA_FIELDS = {
    "quota_credits_total",
    "quota_credits_used",
    "quota_credits_reserved",
}
_ALLOWED_RUNTIME_WRITES_BY_FUNCTION = {
    "app/services/quota.py": {
        "_apply_active_quota_delta": {
            "quota_credits_used",
            "quota_credits_reserved",
        },
        "release_reverse_prompt_video_quota": {"quota_credits_reserved"},
        "settle_reverse_prompt_video_quota": {
            "quota_credits_used",
            "quota_credits_reserved",
        },
        "release_reserved_quota": {"quota_credits_reserved"},
        "settle_reserved_quota": {
            "quota_credits_used",
            "quota_credits_reserved",
        },
    },
    "app/services/admin_console.py": {
        "adjust_credits": {"quota_credits_total"},
    },
}


def _assignment_attributes(node: ast.AST) -> list[ast.Attribute]:
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets.extend(node.targets)
    elif isinstance(node, ast.AugAssign | ast.AnnAssign):
        targets.append(node.target)
    return [target for target in targets if isinstance(target, ast.Attribute)]


def _enclosing_function_name(
    functions: list[ast.FunctionDef | ast.AsyncFunctionDef],
    node: ast.AST,
) -> str:
    if not hasattr(node, "lineno"):
        return "<module>"
    enclosing = [
        function
        for function in functions
        if function.lineno <= node.lineno <= function.end_lineno
    ]
    return max(enclosing, key=lambda function: function.lineno).name if enclosing else "<module>"


def test_runtime_quota_writes_only_occur_in_locked_helpers() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    violations: list[str] = []
    for path in app_root.rglob("*.py"):
        relative = path.relative_to(app_root.parent).as_posix()
        allowed_by_function = _ALLOWED_RUNTIME_WRITES_BY_FUNCTION.get(relative, {})
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for node in ast.walk(tree):
            assignment_attributes = _assignment_attributes(node)
            if assignment_attributes:
                function_name = _enclosing_function_name(functions, node)
                allowed = allowed_by_function.get(function_name, set())
            for target in assignment_attributes:
                if target.attr in _RUNTIME_QUOTA_FIELDS and target.attr not in allowed:
                    violations.append(
                        f"{relative}:{function_name}:{node.lineno}:{target.attr}"
                    )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "values"
            ):
                function_name = _enclosing_function_name(functions, node)
                allowed = allowed_by_function.get(function_name, set())
                for keyword in node.keywords:
                    if keyword.arg in _RUNTIME_QUOTA_FIELDS and keyword.arg not in allowed:
                        violations.append(
                            f"{relative}:{function_name}:{node.lineno}:{keyword.arg}"
                        )

    assert violations == []


def _capture_postgresql_selects(db) -> list[str]:
    statements: list[str] = []

    @event.listens_for(db, "do_orm_execute")
    def _capture(orm_execute_state) -> None:
        if orm_execute_state.is_select:
            statements.append(
                str(orm_execute_state.statement.compile(dialect=postgresql.dialect()))
            )

    return statements


def _seed_sqlite_reservations(db, *, tenant_id: str) -> tuple[str, str]:
    subscription = db.scalar(
        select(Subscription).where(Subscription.tenant_id == tenant_id)
    )
    subscription.quota_credits_reserved = 110
    video = VideoTask(
        id="quota-lock-photo",
        tenant_id=tenant_id,
        status="running",
        mode="photo",
        video_mode="photo",
    )
    reverse = ReversePromptJob(
        id="quota-lock-reverse",
        tenant_id=tenant_id,
        source_kind="video",
        target_format="seedance_2_0",
        status="running",
    )
    db.add_all([video, reverse])
    db.flush()
    db.add_all(
        [
            UsageRecord(
                tenant_id=tenant_id,
                subscription_id=subscription.id,
                video_task_id=video.id,
                capability="image",
                provider="apimart",
                model="gpt-image-2",
                unit="image",
                quantity=Decimal("1"),
                credits=Decimal("10"),
                cost_cents=0,
                status="reserved",
            ),
            UsageRecord(
                tenant_id=tenant_id,
                subscription_id=subscription.id,
                reverse_prompt_job_id=reverse.id,
                capability="reverse_prompt_video",
                provider="apimart",
                model="gemini-3.1-pro-preview",
                unit="call",
                quantity=Decimal("1"),
                credits=Decimal("100"),
                cost_cents=0,
                status="reserved",
            ),
        ]
    )
    db.commit()
    return video.id, reverse.id


def _assert_lock_order(statements: list[str], owner_table: str) -> None:
    owner_index = next(index for index, sql in enumerate(statements) if owner_table in sql)
    usage_index = next(
        index for index, sql in enumerate(statements) if "FROM usage_records" in sql
    )
    subscription_index = next(
        index for index, sql in enumerate(statements) if "FROM subscriptions" in sql
    )
    assert owner_index < usage_index < subscription_index
    assert "FOR UPDATE" in statements[owner_index]
    assert "FOR UPDATE" in statements[usage_index]
    assert "FOR UPDATE" in statements[subscription_index]


def test_quota_transitions_lock_owner_record_then_subscription(
    auth_db,
    auth_context,
) -> None:
    with auth_db() as seed:
        video_id, reverse_id = _seed_sqlite_reservations(
            seed,
            tenant_id=auth_context["tenant_id"],
        )

    with auth_db() as db:
        statements = _capture_postgresql_selects(db)
        quota.release_reserved_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            video_task_id=video_id,
        )
        _assert_lock_order(statements, "FROM video_tasks")

        statements.clear()
        quota.release_reverse_prompt_video_quota(
            db,
            tenant_id=auth_context["tenant_id"],
            reverse_prompt_job_id=reverse_id,
        )
        _assert_lock_order(statements, "FROM reverse_prompt_jobs")


@pytest.fixture(scope="module")
def postgres_session_factory():
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for quota concurrency tests.")

    engine = create_engine(database_url, pool_pre_ping=True)
    schema = f"quota_concurrency_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_engine = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(scoped_engine)
    factory = sessionmaker(bind=scoped_engine, autoflush=False, autocommit=False)
    try:
        yield factory
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def _seed_postgres_reservations(
    factory,
    *,
    count: int,
    stale: bool = False,
) -> tuple[str, list[str]]:
    subscription_id, tenant_id = _seed_postgres_subscription(
        factory,
        total=10_000,
        reserved=100 * count,
    )
    task_ids = _seed_postgres_video_tasks(
        factory,
        tenant_id=tenant_id,
        count=count,
        stale=stale,
    )
    with factory() as db:
        for task_id in task_ids:
            db.add(
                UsageRecord(
                    tenant_id=tenant_id,
                    subscription_id=subscription_id,
                    video_task_id=task_id,
                    capability="image",
                    provider="apimart",
                    model="gpt-image-2",
                    unit="image",
                    quantity=Decimal("1"),
                    credits=Decimal("100"),
                    cost_cents=0,
                    status="reserved",
                )
            )
        db.commit()
    return subscription_id, task_ids


def _seed_postgres_subscription(
    factory,
    *,
    total: int,
    used: int = 0,
    reserved: int = 0,
) -> tuple[str, str]:
    suffix = uuid4().hex[:8]
    tenant_id = str(uuid4())
    now = datetime.now(UTC)
    with factory() as db:
        tenant = Tenant(id=tenant_id, slug=f"quota-{suffix}", name="Quota Concurrency")
        plan = Plan(
            code=f"quota-{suffix}",
            name="Quota Concurrency",
            price_cents=0,
            period="monthly",
            quota_credits=total,
        )
        db.add_all([tenant, plan])
        db.flush()
        subscription = Subscription(
            tenant_id=tenant.id,
            plan_id=plan.id,
            status="active",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=30),
            quota_credits_total=total,
            quota_credits_used=used,
            quota_credits_reserved=reserved,
        )
        db.add(subscription)
        db.commit()
        return subscription.id, tenant.id


def _seed_postgres_video_tasks(
    factory,
    *,
    tenant_id: str,
    count: int,
    stale: bool = False,
) -> list[str]:
    now = datetime.now(UTC)
    with factory() as db:
        task_ids: list[str] = []
        for _ in range(count):
            task = VideoTask(
                id=str(uuid4()),
                tenant_id=tenant_id,
                status="running",
                mode="photo",
                video_mode="photo",
                progress=30,
                started_at=now - timedelta(seconds=1901) if stale else now,
                updated_at=now - timedelta(seconds=1901) if stale else now,
            )
            db.add(task)
            task_ids.append(task.id)
        db.commit()
        return task_ids


def _seed_postgres_reverse_prompt_reservations(
    factory,
    *,
    count: int,
) -> tuple[str, str, list[str]]:
    subscription_id, tenant_id = _seed_postgres_subscription(
        factory,
        total=10_000,
        reserved=100 * count,
    )
    with factory() as db:
        jobs = [
            ReversePromptJob(
                id=str(uuid4()),
                tenant_id=tenant_id,
                source_kind="video",
                target_format="seedance_2_0",
                status="running",
            )
            for _ in range(count)
        ]
        db.add_all(jobs)
        db.flush()
        db.add_all(
            [
                UsageRecord(
                    tenant_id=tenant_id,
                    subscription_id=subscription_id,
                    reverse_prompt_job_id=job.id,
                    capability="reverse_prompt_video",
                    provider="apimart",
                    model="gemini-3.1-pro-preview",
                    unit="call",
                    quantity=Decimal("1"),
                    credits=Decimal("100"),
                    cost_cents=0,
                    status="reserved",
                )
                for job in jobs
            ]
        )
        db.commit()
        return subscription_id, tenant_id, [job.id for job in jobs]


def _seed_postgres_admin_actor(factory, *, tenant_id: str) -> str:
    with factory() as db:
        actor = User(
            tenant_id=tenant_id,
            email=f"quota-admin-{uuid4().hex[:8]}@example.com",
            password_hash="not-used-by-concurrency-test",
            role="admin",
        )
        db.add(actor)
        db.commit()
        return actor.id


def _start_transition(factory, transition):
    done = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            with factory() as db:
                transition(db)
                db.commit()
        except BaseException as exc:  # noqa: BLE001 - thread failures are test evidence.
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, done, errors


def test_postgres_concurrent_copy_charges_preserve_both_increments(
    postgres_session_factory,
) -> None:
    factory = postgres_session_factory
    subscription_id, tenant_id = _seed_postgres_subscription(factory, total=10_000)
    first = factory()
    thread = None
    try:
        quota.charge_copy_quota(first, tenant_id=tenant_id, provider="deepseek")
        thread, done, errors = _start_transition(
            factory,
            lambda db: quota.charge_copy_quota(
                db,
                tenant_id=tenant_id,
                provider="deepseek",
            ),
        )
        second_was_blocked = not done.wait(timeout=0.4)
        first.commit()
        thread.join(timeout=5)
    finally:
        first.rollback()
        first.close()
        if thread is not None:
            thread.join(timeout=5)

    assert second_was_blocked
    assert not thread.is_alive()
    assert errors == []
    with factory() as db:
        subscription = db.get(Subscription, subscription_id)
        records = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == tenant_id,
                    UsageRecord.capability == "llm",
                    UsageRecord.status == "settled",
                )
            )
        )
        assert subscription.quota_credits_used == 2
        assert len(records) == 2
        assert sum(record.credits for record in records) == Decimal("2")


def test_postgres_concurrent_image_reservations_allow_exactly_one_order(
    postgres_session_factory,
) -> None:
    factory = postgres_session_factory
    subscription_id, tenant_id = _seed_postgres_subscription(factory, total=10)
    task_ids = _seed_postgres_video_tasks(factory, tenant_id=tenant_id, count=2)
    first = factory()
    thread = None
    try:
        quota.reserve_image_generation_quota(
            first,
            tenant_id=tenant_id,
            video_task_id=task_ids[0],
        )
        thread, done, errors = _start_transition(
            factory,
            lambda db: quota.reserve_image_generation_quota(
                db,
                tenant_id=tenant_id,
                video_task_id=task_ids[1],
            ),
        )
        second_was_blocked = not done.wait(timeout=0.4)
        first.commit()
        thread.join(timeout=5)
    finally:
        first.rollback()
        first.close()
        if thread is not None:
            thread.join(timeout=5)

    assert second_was_blocked
    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], AppError)
    assert errors[0].code == "TENANT_QUOTA_EXCEEDED"
    with factory() as db:
        subscription = db.get(Subscription, subscription_id)
        records = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.video_task_id.in_(task_ids),
                    UsageRecord.status == "reserved",
                )
            )
        )
        assert subscription.quota_credits_reserved == 10
        assert len(records) == 1


def test_postgres_concurrent_release_preserves_both_decrements(
    postgres_session_factory,
) -> None:
    factory = postgres_session_factory
    subscription_id, task_ids = _seed_postgres_reservations(factory, count=2)
    first = factory()
    thread = None
    try:
        quota.release_reserved_quota(
            first,
            tenant_id=first.get(Subscription, subscription_id).tenant_id,
            video_task_id=task_ids[0],
        )
        tenant_id = first.get(Subscription, subscription_id).tenant_id
        thread, done, errors = _start_transition(
            factory,
            lambda db: quota.release_reserved_quota(
                db,
                tenant_id=tenant_id,
                video_task_id=task_ids[1],
            ),
        )
        second_was_blocked = not done.wait(timeout=0.4)
        first.commit()
        thread.join(timeout=5)
    finally:
        first.rollback()
        first.close()
        if thread is not None:
            thread.join(timeout=5)

    assert second_was_blocked
    assert not thread.is_alive()
    assert errors == []
    with factory() as db:
        subscription = db.get(Subscription, subscription_id)
        records = list(
            db.scalars(
                select(UsageRecord).where(UsageRecord.video_task_id.in_(task_ids))
            )
        )
        assert subscription.quota_credits_reserved == 0
        assert {record.status for record in records} == {"released"}


def test_postgres_concurrent_settlement_preserves_both_transitions(
    postgres_session_factory,
) -> None:
    factory = postgres_session_factory
    subscription_id, task_ids = _seed_postgres_reservations(factory, count=2)
    first = factory()
    thread = None
    try:
        tenant_id = first.get(Subscription, subscription_id).tenant_id
        quota.settle_reserved_quota(
            first,
            tenant_id=tenant_id,
            video_task_id=task_ids[0],
            actual_seconds=1,
            cost_cents=4,
        )
        thread, done, errors = _start_transition(
            factory,
            lambda db: quota.settle_reserved_quota(
                db,
                tenant_id=tenant_id,
                video_task_id=task_ids[1],
                actual_seconds=1,
                cost_cents=4,
            ),
        )
        second_was_blocked = not done.wait(timeout=0.4)
        first.commit()
        thread.join(timeout=5)
    finally:
        first.rollback()
        first.close()
        if thread is not None:
            thread.join(timeout=5)

    assert second_was_blocked
    assert not thread.is_alive()
    assert errors == []
    with factory() as db:
        subscription = db.get(Subscription, subscription_id)
        records = list(
            db.scalars(
                select(UsageRecord).where(UsageRecord.video_task_id.in_(task_ids))
            )
        )
        assert subscription.quota_credits_reserved == 0
        assert subscription.quota_credits_used == 200
        assert {record.status for record in records} == {"settled"}


def test_postgres_concurrent_reverse_prompt_releases_preserve_both_decrements(
    postgres_session_factory,
) -> None:
    factory = postgres_session_factory
    subscription_id, tenant_id, job_ids = _seed_postgres_reverse_prompt_reservations(
        factory,
        count=2,
    )
    first = factory()
    thread = None
    try:
        quota.release_reverse_prompt_video_quota(
            first,
            tenant_id=tenant_id,
            reverse_prompt_job_id=job_ids[0],
        )
        thread, done, errors = _start_transition(
            factory,
            lambda db: quota.release_reverse_prompt_video_quota(
                db,
                tenant_id=tenant_id,
                reverse_prompt_job_id=job_ids[1],
            ),
        )
        second_was_blocked = not done.wait(timeout=0.4)
        first.commit()
        thread.join(timeout=5)
    finally:
        first.rollback()
        first.close()
        if thread is not None:
            thread.join(timeout=5)

    assert not thread.is_alive()
    assert errors == []
    with factory() as db:
        subscription = db.get(Subscription, subscription_id)
        records = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.reverse_prompt_job_id.in_(job_ids)
                )
            )
        )
        assert subscription.quota_credits_reserved == 0
        assert {record.status for record in records} == {"released"}
    assert second_was_blocked


def test_postgres_concurrent_reverse_prompt_settlement_is_idempotent(
    postgres_session_factory,
) -> None:
    factory = postgres_session_factory
    subscription_id, tenant_id, job_ids = _seed_postgres_reverse_prompt_reservations(
        factory,
        count=1,
    )
    job_id = job_ids[0]

    def settle(db) -> None:
        quota.settle_reverse_prompt_video_quota(
            db,
            tenant_id=tenant_id,
            reverse_prompt_job_id=job_id,
            provider="apimart",
            model="gemini-3.1-pro-preview",
            total_tokens=321,
            cost_cents=7,
        )

    first = factory()
    thread = None
    try:
        settle(first)
        thread, done, errors = _start_transition(factory, settle)
        second_was_blocked = not done.wait(timeout=0.4)
        first.commit()
        thread.join(timeout=5)
    finally:
        first.rollback()
        first.close()
        if thread is not None:
            thread.join(timeout=5)

    assert not thread.is_alive()
    assert errors == []
    with factory() as db:
        subscription = db.get(Subscription, subscription_id)
        record = db.scalar(
            select(UsageRecord).where(UsageRecord.reverse_prompt_job_id == job_id)
        )
        assert subscription.quota_credits_reserved == 0
        assert subscription.quota_credits_used == 100
        assert record.status == "settled"
    assert second_was_blocked


def test_postgres_concurrent_admin_credit_adjustments_preserve_both_increments(
    postgres_session_factory,
) -> None:
    factory = postgres_session_factory
    initial_total = 10_000
    increment = 25
    subscription_id, tenant_id = _seed_postgres_subscription(
        factory,
        total=initial_total,
    )
    actor_id = _seed_postgres_admin_actor(factory, tenant_id=tenant_id)

    def adjust(db) -> None:
        actor = db.get(User, actor_id)
        assert actor is not None
        admin_console.adjust_credits(
            db,
            actor=actor,
            tenant_id=tenant_id,
            delta=increment,
            reason="concurrent quota mutation test",
        )

    first = factory()
    thread = None
    try:
        adjust(first)
        thread, done, errors = _start_transition(factory, adjust)
        second_was_blocked = not done.wait(timeout=0.4)
        first.commit()
        thread.join(timeout=5)
    finally:
        first.rollback()
        first.close()
        if thread is not None:
            thread.join(timeout=5)

    assert not thread.is_alive()
    assert errors == []
    with factory() as db:
        subscription = db.get(Subscription, subscription_id)
        assert subscription.quota_credits_total == initial_total + (2 * increment)
    assert second_was_blocked


def test_postgres_recovery_release_wins_against_worker_settlement(
    postgres_session_factory,
    monkeypatch,
) -> None:
    from app.services import task_recovery

    factory = postgres_session_factory
    subscription_id, task_ids = _seed_postgres_reservations(factory, count=1, stale=True)
    task_id = task_ids[0]
    with factory() as db:
        tenant_id = db.get(Subscription, subscription_id).tenant_id

    release_entered = threading.Event()
    allow_recovery_commit = threading.Event()
    recovery_done = threading.Event()
    recovery_errors: list[BaseException] = []
    real_release = task_recovery.release_reserved_quota

    def pausing_release(db, **kwargs) -> None:
        real_release(db, **kwargs)
        release_entered.set()
        assert allow_recovery_commit.wait(timeout=5)

    monkeypatch.setattr(task_recovery, "release_reserved_quota", pausing_release)

    def recover() -> None:
        try:
            task_recovery.recover_orphaned_image_queue_tasks(
                session_factory=factory,
                now=datetime.now(UTC),
                stale_after_seconds=1800,
            )
        except BaseException as exc:  # noqa: BLE001 - thread failures are test evidence.
            recovery_errors.append(exc)
        finally:
            recovery_done.set()

    recovery_thread = threading.Thread(target=recover, daemon=True)
    recovery_thread.start()
    assert release_entered.wait(timeout=5)
    worker_thread, worker_done, worker_errors = _start_transition(
        factory,
        lambda db: quota.settle_reserved_quota(
            db,
            tenant_id=tenant_id,
            video_task_id=task_id,
            actual_seconds=1,
            cost_cents=4,
        ),
    )
    worker_was_blocked = not worker_done.wait(timeout=0.4)
    allow_recovery_commit.set()
    recovery_thread.join(timeout=5)
    worker_thread.join(timeout=5)

    assert worker_was_blocked
    assert not recovery_thread.is_alive()
    assert not worker_thread.is_alive()
    assert recovery_errors == []
    assert worker_errors == []
    with factory() as db:
        subscription = db.get(Subscription, subscription_id)
        task = db.get(VideoTask, task_id)
        record = db.scalar(
            select(UsageRecord).where(UsageRecord.video_task_id == task_id)
        )
        assert subscription.quota_credits_reserved == 0
        assert subscription.quota_credits_used == 0
        assert task.status == "failed"
        assert record.status == "released"
