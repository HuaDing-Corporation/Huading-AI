from __future__ import annotations

import ast
import os
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import AppError
from app.db.models import (
    Base,
    BillingOperation,
    CreditRefundGrant,
    Plan,
    ReversePromptJob,
    Subscription,
    Tenant,
    UsageRecord,
    User,
    VideoTask,
)
from app.services import admin_console, quota
from app.services.billing_operations import (
    UsageAllocation,
    complete_failed,
    complete_succeeded,
    create_reserved_operation,
    register_billing_result_schema,
)
from app.services.billing_quotes import VerifiedQuote
from app.services.pricing import (
    PricingLine,
    PricingSnapshot,
    RateScope,
    RateSource,
    ResolvedRate,
)
from app.services.subscription import (
    activate_subscription,
    activate_subscription_with_retry,
    decide_credit_refund,
    lock_tenant_for_subscription_lifecycle,
    refund_subscriptions_for_update,
)
from app.services.transaction_retry import run_db_transaction_with_retry

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
        "release_copy_quota": {"quota_credits_reserved"},
        "settle_copy_quota": {
            "quota_credits_used",
            "quota_credits_reserved",
        },
        "settle_reserved_quota": {
            "quota_credits_used",
            "quota_credits_reserved",
        },
        "reserve_locked_subscription_credits": {"quota_credits_reserved"},
        "settle_locked_subscription_credits": {
            "quota_credits_used",
            "quota_credits_reserved",
        },
    },
    "app/services/admin_console.py": {
        "adjust_credits": {"quota_credits_total"},
    },
    "app/services/subscription.py": {
        "apply_pending_refund_grants": {"quota_credits_total"},
        "decide_credit_refund": {"quota_credits_total"},
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
        function for function in functions if function.lineno <= node.lineno <= function.end_lineno
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
                    violations.append(f"{relative}:{function_name}:{node.lineno}:{target.attr}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "values"
            ):
                function_name = _enclosing_function_name(functions, node)
                allowed = allowed_by_function.get(function_name, set())
                for keyword in node.keywords:
                    if keyword.arg in _RUNTIME_QUOTA_FIELDS and keyword.arg not in allowed:
                        violations.append(f"{relative}:{function_name}:{node.lineno}:{keyword.arg}")

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
    subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
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
    usage_index = next(index for index, sql in enumerate(statements) if "FROM usage_records" in sql)
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


def test_admin_subscription_mutations_lock_tenant_before_subscription(
    auth_db,
    auth_context,
) -> None:
    with auth_db() as db:
        actor = db.get(User, auth_context["user_id"])
        db.add(
            Plan(
                code="free",
                name="Lock Order Target",
                price_cents=1,
                period="monthly",
                quota_credits=123,
            )
        )
        db.commit()

    with auth_db() as db:
        actor = db.get(User, auth_context["user_id"])
        statements = _capture_postgresql_selects(db)
        admin_console.change_plan(
            db,
            actor=actor,
            tenant_id=auth_context["tenant_id"],
            plan_code="free",
            reason="lock audit",
        )
        locked = [sql for sql in statements if "FOR UPDATE" in sql]
        assert "FROM tenants" in locked[0]
        assert "FROM subscriptions" in locked[1]
        assert "ORDER BY subscriptions.id" in locked[1]

    with auth_db() as db:
        actor = db.get(User, auth_context["user_id"])
        statements = _capture_postgresql_selects(db)
        admin_console.adjust_credits(
            db,
            actor=actor,
            tenant_id=auth_context["tenant_id"],
            delta=1,
            reason="lock audit",
        )
        locked = [sql for sql in statements if "FOR UPDATE" in sql]
        assert "FROM tenants" in locked[0]
        assert "FROM subscriptions" in locked[1]
        assert "ORDER BY subscriptions.id" in locked[1]


def test_activation_locks_tenant_subscription_and_grants_in_order(
    auth_db,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        source = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        source.status = "expired"
        source.period_end = now - timedelta(seconds=1)
        plan_id = source.plan_id
        db.commit()

    with auth_db() as db:
        statements = _capture_postgresql_selects(db)
        activate_subscription(
            db,
            tenant_id=auth_context["tenant_id"],
            plan=db.get(Plan, plan_id),
            period_start=now,
        )
        locked = [sql for sql in statements if "FOR UPDATE" in sql]
        assert "FROM tenants" in locked[0]
        assert "FROM subscriptions" in locked[1]
        assert "FROM credit_refund_grants" in locked[2]
        assert "ORDER BY subscriptions.id" in locked[1]
        assert "ORDER BY credit_refund_grants.id" in locked[2]


def test_refund_decision_composes_with_full_global_lock_order(
    auth_db,
    auth_context,
) -> None:
    now = datetime.now(UTC)
    with auth_db() as db:
        target = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        source = Subscription(
            tenant_id=auth_context["tenant_id"],
            plan_id=target.plan_id,
            status="expired",
            period_start=now - timedelta(days=60),
            period_end=now - timedelta(days=30),
            quota_credits_total=30_000,
            quota_credits_used=0,
            quota_credits_reserved=30_000,
        )
        operation = BillingOperation(
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="doubao_brand_voice_order_create",
            idempotency_key=str(uuid4()),
            request_hash="a" * 64,
            quote_hash="b" * 64,
            pricing_snapshot={},
            requested_credits=Decimal("30000"),
            settled_credits=Decimal("0"),
            released_credits=Decimal("30000"),
            status="completed",
            completion_kind="rejected",
            completed_at=now,
        )
        db.add_all([source, operation])
        db.flush()
        usage = UsageRecord(
            tenant_id=auth_context["tenant_id"],
            subscription_id=source.id,
            billing_operation_id=operation.id,
            billing_item_index=0,
            billing_pricing_line_index=0,
            capability="voice_clone",
            provider="manual",
            unit="call",
            quantity=Decimal("1"),
            credits=Decimal("30000"),
            cost_cents=0,
            status="reserved",
        )
        db.add(usage)
        db.commit()
        source_id = source.id
        operation_id = operation.id

    with auth_db() as db:
        statements = _capture_postgresql_selects(db)
        lock_tenant_for_subscription_lifecycle(
            db,
            tenant_id=auth_context["tenant_id"],
        )
        locked_operation = db.scalar(
            select(BillingOperation)
            .where(BillingOperation.id == operation_id)
            .order_by(BillingOperation.id)
            .with_for_update()
        )
        context = refund_subscriptions_for_update(
            db,
            tenant_id=auth_context["tenant_id"],
            source_subscription_id=source_id,
            now=now,
        )
        db.scalars(
            select(UsageRecord)
            .where(UsageRecord.billing_operation_id == operation_id)
            .order_by(UsageRecord.id)
            .with_for_update()
        ).all()
        decide_credit_refund(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            billing_operation=locked_operation,
            subscriptions=context,
            amount_credits=30_000,
            decided_at=now,
        )

        locked = [sql for sql in statements if "FOR UPDATE" in sql]
        assert [
            "FROM tenants" in locked[0],
            "FROM billing_operations" in locked[1],
            "FROM subscriptions" in locked[2],
            "FROM usage_records" in locked[3],
            "FROM credit_refund_grants" in locked[4],
        ] == [True] * 5
        assert "ORDER BY billing_operations.id" in locked[1]
        assert "ORDER BY subscriptions.id" in locked[2]
        assert "ORDER BY usage_records.id" in locked[3]
        assert "ORDER BY credit_refund_grants.id" in locked[4]

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


def test_postgres_three_copy_reservations_with_one_credit_allow_exactly_one(
    postgres_session_factory,
) -> None:
    factory = postgres_session_factory
    subscription_id, tenant_id = _seed_postgres_subscription(
        factory,
        total=3,
        used=2,
    )
    start = threading.Event()
    done = [threading.Event() for _ in range(3)]
    errors: list[BaseException] = []
    reservation_ids: list[str] = []

    def reserve(index: int) -> None:
        try:
            assert start.wait(timeout=5)
            with factory() as db:
                reservation = quota.reserve_copy_quota(
                    db,
                    tenant_id=tenant_id,
                    provider="deepseek",
                )
                reservation_ids.append(reservation.usage_record.id)
                db.commit()
        except BaseException as exc:  # noqa: BLE001 - thread failures are evidence.
            errors.append(exc)
        finally:
            done[index].set()

    threads = [threading.Thread(target=reserve, args=(index,), daemon=True) for index in range(3)]
    for thread in threads:
        thread.start()
    start.set()
    for event_ in done:
        assert event_.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(reservation_ids) == 1
    assert len(errors) == 2
    assert all(isinstance(error, AppError) for error in errors)
    assert {error.code for error in errors if isinstance(error, AppError)} == {
        "TENANT_QUOTA_EXCEEDED"
    }
    with factory() as db:
        subscription = db.get(Subscription, subscription_id)
        records = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == tenant_id,
                    UsageRecord.capability == "llm",
                )
            )
        )
        assert subscription.quota_credits_used == 2
        assert subscription.quota_credits_reserved == 1
        assert len(records) == 1
        assert records[0].id == reservation_ids[0]
        assert records[0].status == "reserved"


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
            resolution="1k",
        )
        thread, done, errors = _start_transition(
            factory,
            lambda db: quota.reserve_image_generation_quota(
                db,
                tenant_id=tenant_id,
                video_task_id=task_ids[1],
                resolution="1k",
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
            db.scalars(select(UsageRecord).where(UsageRecord.video_task_id.in_(task_ids)))
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
            db.scalars(select(UsageRecord).where(UsageRecord.video_task_id.in_(task_ids)))
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
            db.scalars(select(UsageRecord).where(UsageRecord.reverse_prompt_job_id.in_(job_ids)))
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
        record = db.scalar(select(UsageRecord).where(UsageRecord.reverse_prompt_job_id == job_id))
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
        record = db.scalar(select(UsageRecord).where(UsageRecord.video_task_id == task_id))
        assert subscription.quota_credits_reserved == 0
        assert subscription.quota_credits_used == 0
        assert task.status == "failed"
        assert record.status == "released"


class _ConcurrentBillingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_ids: list[str]


def _concurrent_billing_quote() -> VerifiedQuote:
    rate = ResolvedRate(
        unit_credits=Decimal("0.6000"),
        source=RateSource.TENANT_RATE,
        rate_id="concurrent-rate",
        effective_at=datetime(2026, 8, 29, tzinfo=UTC),
        policy_key=None,
        policy_version=None,
    )
    line = PricingLine(
        operation="ecom_cutout",
        capability="image",
        unit="image",
        quantity=Decimal("4"),
        unit_credits=rate.unit_credits,
        subtotal_credits=Decimal("2.4"),
        rate_scope=RateScope.TENANT_OVERRIDABLE,
        rate=rate,
        label="concurrent batch",
    )
    return VerifiedQuote(
        snapshot=PricingSnapshot(
            operation="ecom_cutout",
            pricing_shape="simple",
            pricing_lines=(line,),
            disclosures=(),
            subtotal_credits=Decimal("2.4"),
            payable_credits=3,
        ),
        quote_hash="b" * 64,
        pricing_payload_hash="c" * 64,
    )


def _concurrent_billing_allocations() -> list[UsageAllocation]:
    return [
        UsageAllocation(index, 0, Decimal("1"), Decimal("0.6"), "apimart", None, None)
        for index in range(4)
    ]


def _seed_postgres_billing_owner(factory) -> tuple[str, str]:
    subscription_id, tenant_id = _seed_postgres_subscription(factory, total=100)
    with factory() as db:
        user = User(
            tenant_id=tenant_id,
            email=f"billing-{uuid4().hex[:8]}@example.com",
            password_hash="hash",
            role="creator",
        )
        db.add(user)
        db.commit()
        return tenant_id, user.id


def test_operation_transitions_discover_ids_then_lock_operation_subscription_usage(
    auth_db,
    auth_context,
) -> None:
    with auth_db() as db:
        operation = create_reserved_operation(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            operation="ecom_cutout",
            idempotency_key=uuid4(),
            request_hash="a" * 64,
            verified_quote=_concurrent_billing_quote(),
            usage_allocations=_concurrent_billing_allocations(),
        )
        db.commit()
        operation_id = operation.id

    with auth_db() as db:
        statements = _capture_postgresql_selects(db)
        complete_failed(
            db,
            operation_id=operation_id,
            code="PROVIDER_FAILED",
            http_status=502,
            sanitized_detail=None,
        )

    operation_index = next(
        index for index, sql in enumerate(statements) if "FROM billing_operations" in sql
    )
    usage_indexes = [index for index, sql in enumerate(statements) if "FROM usage_records" in sql]
    subscription_index = next(
        index for index, sql in enumerate(statements) if "FROM subscriptions" in sql
    )
    assert len(usage_indexes) == 2
    discovery_index, locked_usage_index = usage_indexes
    assert discovery_index < operation_index < subscription_index < locked_usage_index
    assert "FOR UPDATE" not in statements[discovery_index]
    assert "FOR UPDATE" in statements[operation_index]
    assert "FOR UPDATE" in statements[subscription_index]
    assert "ORDER BY subscriptions.id" in statements[subscription_index]
    assert "FOR UPDATE" in statements[locked_usage_index]
    assert "ORDER BY usage_records.id" in statements[locked_usage_index]


def test_postgres_concurrent_first_submission_reserves_once(
    postgres_session_factory,
) -> None:
    factory = postgres_session_factory
    tenant_id, user_id = _seed_postgres_billing_owner(factory)
    quote = _concurrent_billing_quote()
    key = uuid4()
    barrier = threading.Barrier(2)
    operation_ids: list[str] = []
    errors: list[BaseException] = []

    def reserve() -> None:
        try:

            def transaction(db):
                barrier.wait(timeout=5)
                return create_reserved_operation(
                    db,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    operation="ecom_cutout",
                    idempotency_key=key,
                    request_hash="a" * 64,
                    verified_quote=quote,
                    usage_allocations=_concurrent_billing_allocations(),
                )

            operation = run_db_transaction_with_retry(factory, transaction)
            operation_ids.append(operation.id)
        except BaseException as exc:  # noqa: BLE001 - thread evidence.
            errors.append(exc)

    threads = [threading.Thread(target=reserve, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(set(operation_ids)) == 1
    with factory() as db:
        subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
        assert subscription.quota_credits_reserved == 3
        assert (
            db.scalar(
                select(func.count())
                .select_from(BillingOperation)
                .where(BillingOperation.tenant_id == tenant_id)
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(UsageRecord)
                .where(UsageRecord.billing_operation_id == operation_ids[0])
            )
            == 4
        )


def test_postgres_concurrent_activation_applies_pending_grant_once(
    postgres_session_factory,
) -> None:
    factory = postgres_session_factory
    tenant_id, user_id = _seed_postgres_billing_owner(factory)
    now = datetime.now(UTC)
    with factory() as db:
        source = db.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        plan_id = source.plan_id
        plan_quota = source.quota_credits_total
        source.status = "expired"
        source.period_end = now - timedelta(seconds=1)
        operation = BillingOperation(
            tenant_id=tenant_id,
            user_id=user_id,
            operation="doubao_brand_voice_order_create",
            idempotency_key=str(uuid4()),
            request_hash="a" * 64,
            quote_hash="b" * 64,
            pricing_snapshot={},
            requested_credits=Decimal("30000"),
            settled_credits=Decimal("0"),
            released_credits=Decimal("30000"),
            status="completed",
            completion_kind="rejected",
            completed_at=now,
        )
        db.add(operation)
        db.flush()
        grant = CreditRefundGrant(
            billing_operation_id=operation.id,
            tenant_id=tenant_id,
            user_id=user_id,
            source_subscription_id=source.id,
            amount_credits=30_000,
            status="pending",
        )
        db.add(grant)
        db.commit()
        grant_id = grant.id

    barrier = threading.Barrier(2)
    activated_ids: list[str] = []
    errors: list[BaseException] = []

    def activate() -> None:
        try:
            barrier.wait(timeout=5)
            activated = activate_subscription_with_retry(
                factory,
                tenant_id=tenant_id,
                plan_id=plan_id,
                period_start=now,
            )
            activated_ids.append(activated.id)
        except BaseException as exc:  # noqa: BLE001 - thread evidence.
            errors.append(exc)

    threads = [threading.Thread(target=activate, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(set(activated_ids)) == 1
    with factory() as db:
        active = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == tenant_id,
                Subscription.status == "active",
            )
        )
        stored_grant = db.get(CreditRefundGrant, grant_id)
        assert active.id == activated_ids[0]
        assert active.quota_credits_total == plan_quota + 30_000
        assert stored_grant.status == "applied"
        assert stored_grant.target_subscription_id == active.id


def test_postgres_competing_settle_and_release_use_one_terminal_transition(
    postgres_session_factory,
) -> None:
    factory = postgres_session_factory
    tenant_id, user_id = _seed_postgres_billing_owner(factory)
    register_billing_result_schema("concurrent_billing_result", _ConcurrentBillingResult)
    operation = run_db_transaction_with_retry(
        factory,
        lambda db: create_reserved_operation(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            operation="ecom_cutout",
            idempotency_key=uuid4(),
            request_hash="a" * 64,
            verified_quote=_concurrent_billing_quote(),
            usage_allocations=_concurrent_billing_allocations(),
        ),
    )

    first = factory()
    release_thread = None
    try:
        complete_succeeded(
            first,
            operation_id=operation.id,
            actual_quantities={0: Decimal("1")},
            result_type="concurrent_billing_result",
            result_id=None,
            result_payload=_ConcurrentBillingResult(item_ids=["a"]),
        )
        release_thread, done, errors = _start_transition(
            factory,
            lambda db: complete_failed(
                db,
                operation_id=operation.id,
                code="PROVIDER_FAILED",
                http_status=502,
                sanitized_detail=None,
            ),
        )
        release_was_blocked = not done.wait(timeout=0.4)
        first.commit()
        release_thread.join(timeout=5)
    finally:
        first.rollback()
        first.close()
        if release_thread is not None:
            release_thread.join(timeout=5)

    assert release_was_blocked
    assert not release_thread.is_alive()
    assert errors == []
    with factory() as db:
        stored = db.get(BillingOperation, operation.id)
        subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
        assert stored.completion_kind == "succeeded"
        assert stored.settled_credits == 1
        assert stored.released_credits == 2
    assert subscription.quota_credits_reserved == 0
    assert subscription.quota_credits_used == 1


def test_postgres_concurrent_last_ecom_items_finalize_the_parent_once(
    postgres_session_factory,
) -> None:
    from app.services.ecom_billing import try_finalize_ecom_operation

    factory = postgres_session_factory
    tenant_id, user_id = _seed_postgres_billing_owner(factory)
    quote = _concurrent_billing_quote()
    with factory() as db:
        tasks = [
            VideoTask(
                id=str(uuid4()),
                tenant_id=tenant_id,
                created_by_user_id=user_id,
                status="queued",
                mode="photo",
                video_mode="photo",
                topic="ecom",
                params={"source_asset_id": f"source-{index}"},
            )
            for index in range(4)
        ]
        db.add_all(tasks)
        db.flush()
        operation = create_reserved_operation(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            operation="ecom_cutout",
            idempotency_key=uuid4(),
            request_hash="d" * 64,
            verified_quote=quote,
            usage_allocations=tuple(
                UsageAllocation(index, 0, Decimal("1"), Decimal("0.6"), "apimart", None, task.id)
                for index, task in enumerate(tasks)
            ),
            result_type="ecom_image_batch",
            result_id="concurrent-ecom",
        )
        for index, task in enumerate(tasks):
            task.params = {
                **task.params,
                "billing_operation_id": operation.id,
                "billing_item_index": index,
            }
            task.status = "failed" if index < 2 else "queued"
        db.commit()
        operation_id = operation.id
        final_task_ids = [task.id for task in tasks[2:]]

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def finish(task_id: str) -> None:
        try:
            with factory() as db:
                task = db.get(VideoTask, task_id)
                assert task is not None
                task.status = "failed"
                db.commit()
            barrier.wait(timeout=5)
            with factory() as db:
                try_finalize_ecom_operation(db, billing_operation_id=operation_id)
        except BaseException as exc:  # noqa: BLE001 - concurrency evidence
            errors.append(exc)

    threads = [
        threading.Thread(target=finish, args=(task_id,), daemon=True)
        for task_id in final_task_ids
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    with factory() as db:
        stored = db.get(BillingOperation, operation_id)
        subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    assert stored is not None
    assert stored.status == "completed"
    assert stored.completion_kind == "failed"
    assert stored.released_credits == 3
    assert subscription.quota_credits_reserved == 0
