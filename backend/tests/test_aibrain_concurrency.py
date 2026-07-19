import ast
import asyncio
import os
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, event, insert, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import AppError
from app.db.models import (
    Base,
    ChatConversation,
    ChatMessage,
    Plan,
    ReasoningLedgerEntry,
    ReasoningWallet,
    Subscription,
    Tenant,
    UsageRecord,
    User,
)
from app.db.reasoning_wallet_guard import allow_reasoning_wallet_mutation
from app.services import aibrain

_WALLET_MONETARY_FIELDS = {
    "available_credits",
    "reserved_credits",
    "total_topup_credits",
    "total_spent_credits",
}
_STATIC_ALLOWED_WRITES_BY_FUNCTION = {
    "app/services/aibrain.py": {
        "_apply_reasoning_wallet_change": _WALLET_MONETARY_FIELDS,
    }
}


@allow_reasoning_wallet_mutation
def _execute_inside_wallet_mutation_window(db, statement) -> None:
    db.execute(statement)


def _assignment_attributes(node: ast.AST) -> list[ast.Attribute]:
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets.extend(node.targets)
    elif isinstance(node, ast.AugAssign | ast.AnnAssign):
        targets.append(node.target)
    return [target for target in targets if isinstance(target, ast.Attribute)]


def _mentions_reasoning_wallet(node: ast.AST | None) -> bool:
    return node is not None and any(
        isinstance(item, ast.Name) and item.id == "ReasoningWallet" for item in ast.walk(node)
    )


def _literal_dict_keys(
    node: ast.AST,
    dictionaries: dict[str, set[str]],
) -> set[str]:
    if isinstance(node, ast.Name):
        return dictionaries.get(node.id, set())
    if not isinstance(node, ast.Dict):
        return set()
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _function_wallet_bindings(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[set[str], dict[str, set[str]]]:
    wallet_names = {
        argument.arg
        for argument in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
        if _mentions_reasoning_wallet(argument.annotation)
    }
    dictionaries: dict[str, set[str]] = {}
    assignments = [
        node for node in ast.walk(function) if isinstance(node, ast.Assign | ast.AnnAssign)
    ]
    changed = True
    while changed:
        changed = False
        for assignment in assignments:
            value = assignment.value
            targets = (
                assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            )
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                keys = _literal_dict_keys(value, dictionaries)
                known_keys = dictionaries.get(target.id, set())
                if keys - known_keys:
                    dictionaries[target.id] = known_keys | keys
                    changed = True
                is_wallet = _mentions_reasoning_wallet(value) or (
                    isinstance(value, ast.Name) and value.id in wallet_names
                )
                if isinstance(assignment, ast.AnnAssign) and _mentions_reasoning_wallet(
                    assignment.annotation
                ):
                    is_wallet = True
                if is_wallet and target.id not in wallet_names:
                    wallet_names.add(target.id)
                    changed = True
    return wallet_names, dictionaries


def _wallet_update_fields(
    node: ast.Call,
    dictionaries: dict[str, set[str]],
) -> set[str]:
    if (
        not isinstance(node.func, ast.Attribute)
        or node.func.attr != "values"
        or not _mentions_reasoning_wallet(node.func.value)
    ):
        return set()
    fields = {keyword.arg for keyword in node.keywords if keyword.arg in _WALLET_MONETARY_FIELDS}
    for keyword in node.keywords:
        if keyword.arg is None:
            fields.update(_literal_dict_keys(keyword.value, dictionaries))
    for argument in node.args:
        fields.update(_literal_dict_keys(argument, dictionaries))
    return fields & _WALLET_MONETARY_FIELDS


def _static_wallet_write_hints(app_root: Path) -> list[str]:
    """Find obvious writes early; this is not the wallet's security boundary.

    Dynamic setattr fields, runtime mappings, reflected aliases, and raw SQL cannot be
    proven safe by this AST pass. Runtime ORM/Core events enforce the supported paths.
    """
    violations: list[str] = []
    for path in app_root.rglob("*.py"):
        relative = path.relative_to(app_root.parent).as_posix()
        allowed_by_function = _STATIC_ALLOWED_WRITES_BY_FUNCTION.get(relative, {})
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        bindings = {id(function): _function_wallet_bindings(function) for function in functions}
        for node in ast.walk(tree):
            assignment_attributes = _assignment_attributes(node)
            function = next(
                (
                    item
                    for item in sorted(functions, key=lambda value: value.lineno, reverse=True)
                    if item.lineno <= getattr(node, "lineno", -1) <= item.end_lineno
                ),
                None,
            )
            function_name = function.name if function is not None else "<module>"
            allowed = allowed_by_function.get(function_name, set())
            wallet_names, dictionaries = bindings.get(id(function), (set(), {}))
            for target in assignment_attributes:
                if (
                    isinstance(target.value, ast.Name)
                    and target.value.id in wallet_names
                    and target.attr in _WALLET_MONETARY_FIELDS
                    and target.attr not in allowed
                ):
                    violations.append(f"{relative}:{function_name}:{node.lineno}:{target.attr}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in wallet_names
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in _WALLET_MONETARY_FIELDS
                and node.args[1].value not in allowed
            ):
                violations.append(f"{relative}:{function_name}:{node.lineno}:{node.args[1].value}")
            if isinstance(node, ast.Call):
                for field in _wallet_update_fields(node, dictionaries) - allowed:
                    violations.append(f"{relative}:{function_name}:{node.lineno}:{field}")

    return violations


def _write_wallet_guard_probe(tmp_path: Path, source: str) -> Path:
    app_root = tmp_path / "app"
    probe = app_root / "services" / "wallet_guard_probe.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(source, encoding="utf-8")
    return app_root


def test_static_wallet_hint_rejects_an_aliased_wallet_assignment(tmp_path: Path) -> None:
    app_root = _write_wallet_guard_probe(
        tmp_path,
        """
from app.db.models import ReasoningWallet

def bypass(wallet: ReasoningWallet) -> None:
    account = wallet
    account.available_credits = 999
""",
    )

    assert _static_wallet_write_hints(app_root)


def test_static_wallet_hint_rejects_setattr_on_a_wallet(tmp_path: Path) -> None:
    app_root = _write_wallet_guard_probe(
        tmp_path,
        """
from app.db.models import ReasoningWallet

def bypass(obj: ReasoningWallet) -> None:
    setattr(obj, "available_credits", 999)
""",
    )

    assert _static_wallet_write_hints(app_root)


def test_static_wallet_hint_rejects_unpacked_bulk_wallet_updates(tmp_path: Path) -> None:
    app_root = _write_wallet_guard_probe(
        tmp_path,
        """
from sqlalchemy import update
from app.db.models import ReasoningWallet

def bypass() -> None:
    fields = {"available_credits": 999}
    update(ReasoningWallet).values(**fields)
""",
    )

    assert _static_wallet_write_hints(app_root)


def test_static_wallet_hints_only_allow_the_locked_helper() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"

    assert _static_wallet_write_hints(app_root) == []


def test_runtime_guard_rejects_dynamic_setattr_on_a_wallet(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("100"),
        )
        db.commit()
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        field_name = "available_credits"

        with pytest.raises(RuntimeError, match="locked AIBRAIN wallet helper"):
            setattr(wallet, field_name, Decimal("999"))


def test_runtime_guard_rejects_dynamic_core_wallet_update(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("100"),
        )
        db.commit()
        payload = {"available_credits": Decimal("999")}

        with pytest.raises(RuntimeError, match="locked AIBRAIN wallet helper"):
            db.execute(
                update(ReasoningWallet)
                .where(ReasoningWallet.tenant_id == auth_context["tenant_id"])
                .values(**payload)
            )


def test_runtime_guard_rejects_reflected_wallet_descriptor_write(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("100"),
        )
        db.commit()
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        field_name = "available_credits"
        descriptor = getattr(ReasoningWallet, field_name)

        with pytest.raises(RuntimeError, match="locked AIBRAIN wallet helper"):
            descriptor.__set__(wallet, Decimal("999"))


def test_runtime_guard_rejects_direct_wallet_balance_construction(
    auth_context,
) -> None:
    with pytest.raises(RuntimeError, match="locked AIBRAIN wallet helper"):
        ReasoningWallet(
            tenant_id=auth_context["tenant_id"],
            available_credits=Decimal("999"),
            reserved_credits=Decimal("0"),
            total_topup_credits=Decimal("999"),
            total_spent_credits=Decimal("0"),
        )


def test_runtime_guard_rejects_typed_core_wallet_insert(
    auth_context,
    auth_db,
) -> None:
    payload = {
        "tenant_id": auth_context["tenant_id"],
        "available_credits": Decimal("999"),
        "reserved_credits": Decimal("0"),
        "total_topup_credits": Decimal("999"),
        "total_spent_credits": Decimal("0"),
    }
    with auth_db() as db:
        with pytest.raises(RuntimeError, match="locked AIBRAIN wallet helper"):
            db.execute(insert(ReasoningWallet).values(**payload))


def test_runtime_guard_rejects_typed_core_wallet_delete(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("100"),
        )
        db.commit()

        with pytest.raises(RuntimeError, match="locked AIBRAIN wallet helper"):
            db.execute(
                delete(ReasoningWallet).where(
                    ReasoningWallet.tenant_id == auth_context["tenant_id"]
                )
            )


def test_runtime_guard_rejects_orm_wallet_delete(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("100"),
        )
        db.commit()
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])

        db.delete(wallet)
        with pytest.raises(RuntimeError, match="locked AIBRAIN wallet helper"):
            db.flush()


def test_runtime_guard_rejects_typed_core_ledger_delete(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        mutation = aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("100"),
        )
        ledger_entry_id = mutation.ledger_entry.id
        db.commit()

        with pytest.raises(RuntimeError, match="append-only"):
            db.execute(
                delete(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.id == ledger_entry_id
                )
            )


def test_runtime_guard_rejects_orm_ledger_delete(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        mutation = aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("100"),
        )
        ledger_entry_id = mutation.ledger_entry.id
        db.commit()
        ledger_entry = db.get(ReasoningLedgerEntry, ledger_entry_id)

        db.delete(ledger_entry)
        with pytest.raises(RuntimeError, match="append-only"):
            db.flush()


def test_runtime_guard_rejects_typed_core_ledger_update(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        mutation = aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("100"),
        )
        ledger_entry_id = mutation.ledger_entry.id
        db.commit()

        with pytest.raises(RuntimeError, match="append-only"):
            db.execute(
                update(ReasoningLedgerEntry)
                .where(ReasoningLedgerEntry.id == ledger_entry_id)
                .values(details={"tampered": True})
            )


def test_runtime_guard_rejects_typed_core_ledger_insert_outside_helper(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("100"),
        )
        db.commit()
        payload = {
            "id": str(uuid4()),
            "tenant_id": auth_context["tenant_id"],
            "entry_type": "release",
            "amount_credits": Decimal("1"),
            "available_delta": Decimal("1"),
            "reserved_delta": Decimal("-1"),
            "available_after": Decimal("101"),
            "reserved_after": Decimal("0"),
            "details": {"forged": True},
        }

        with pytest.raises(RuntimeError, match="locked AIBRAIN wallet helper"):
            db.execute(insert(ReasoningLedgerEntry).values(**payload))


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_runtime_guard_rejects_ledger_rewrites_inside_helper_window(
    auth_context,
    auth_db,
    operation: str,
) -> None:
    with auth_db() as db:
        mutation = aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("100"),
        )
        ledger_entry_id = mutation.ledger_entry.id
        db.commit()
        statement = (
            update(ReasoningLedgerEntry)
            .where(ReasoningLedgerEntry.id == ledger_entry_id)
            .values(details={"tampered": True})
            if operation == "update"
            else delete(ReasoningLedgerEntry).where(
                ReasoningLedgerEntry.id == ledger_entry_id
            )
        )

        with pytest.raises(RuntimeError, match="append-only"):
            _execute_inside_wallet_mutation_window(db, statement)


def test_topup_ledger_cannot_be_deleted_to_replay_primary_quota_debit(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    idempotency_key = uuid4()
    with auth_db() as db:
        first_response = aibrain.top_up_reasoning_wallet(
            db,
            tenant_id=tenant_id,
            amount=100,
            idempotency_key=idempotency_key,
        )
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        topup_entry = db.scalar(
            select(ReasoningLedgerEntry).where(
                ReasoningLedgerEntry.tenant_id == tenant_id,
                ReasoningLedgerEntry.entry_type == "topup",
            )
        )
        assert subscription.quota_credits_used == 100

        db.delete(topup_entry)
        with pytest.raises(RuntimeError, match="append-only"):
            db.flush()
        db.rollback()

    with auth_db() as db:
        replay_response = aibrain.top_up_reasoning_wallet(
            db,
            tenant_id=tenant_id,
            amount=100,
            idempotency_key=idempotency_key,
        )
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        wallet = db.get(ReasoningWallet, tenant_id)
        topups = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.tenant_id == tenant_id,
                    ReasoningLedgerEntry.entry_type == "topup",
                )
            )
        )

        assert replay_response == first_response
        assert subscription.quota_credits_used == 100
        assert wallet.available_credits == Decimal("100")
        assert wallet.total_topup_credits == Decimal("100")
        assert len(topups) == 1


def test_wallet_mutation_query_uses_for_update_and_populate_existing(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        statements: list[tuple[str, bool]] = []

        @event.listens_for(db, "do_orm_execute")
        def _capture(orm_execute_state) -> None:
            if orm_execute_state.is_select:
                statements.append(
                    (
                        str(orm_execute_state.statement.compile(dialect=postgresql.dialect())),
                        bool(orm_execute_state.execution_options.get("populate_existing", False)),
                    )
                )

        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("100"),
        )

    wallet_selects = [item for item in statements if "FROM reasoning_wallets" in item[0]]
    assert any("FOR UPDATE" in statement for statement, _refresh in wallet_selects)
    assert any(refresh for _statement, refresh in wallet_selects)


@pytest.fixture(scope="module")
def postgres_session_factory():
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for AIBRAIN concurrency tests.")

    engine = create_engine(database_url, pool_pre_ping=True)
    schema = f"aibrain_concurrency_{uuid4().hex}"
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


def _seed_postgres_wallet(factory, *, available: Decimal) -> str:
    tenant_id = str(uuid4())
    with factory() as db:
        db.add(
            Tenant(
                id=tenant_id,
                slug=f"aibrain-{uuid4().hex[:10]}",
                name="AIBRAIN Concurrency",
            )
        )
        db.flush()
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=tenant_id,
            entry_type="topup",
            amount_credits=available,
            operation_key=f"seed-topup:{tenant_id}",
        )
        db.commit()
    return tenant_id


def _start_reservation(factory, *, tenant_id: str, operation_key: str):
    done = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            with factory() as db:
                aibrain._apply_reasoning_wallet_change(
                    db,
                    tenant_id=tenant_id,
                    entry_type="reserve",
                    amount_credits=Decimal("200"),
                    operation_key=operation_key,
                )
                db.commit()
        except BaseException as exc:  # noqa: BLE001 - thread failures are evidence.
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, done, errors


def test_postgres_concurrent_reservations_allow_exactly_one_request(
    postgres_session_factory,
) -> None:
    factory = postgres_session_factory
    tenant_id = _seed_postgres_wallet(factory, available=Decimal("200"))
    first = factory()
    second_thread = None
    try:
        aibrain._apply_reasoning_wallet_change(
            first,
            tenant_id=tenant_id,
            entry_type="reserve",
            amount_credits=Decimal("200"),
            operation_key="first-reservation",
        )
        second_thread, second_done, second_errors = _start_reservation(
            factory,
            tenant_id=tenant_id,
            operation_key="second-reservation",
        )
        second_was_blocked = not second_done.wait(timeout=0.4)
        first.commit()
        second_thread.join(timeout=5)
    finally:
        first.rollback()
        first.close()
        if second_thread is not None:
            second_thread.join(timeout=5)

    assert second_was_blocked
    assert not second_thread.is_alive()
    assert len(second_errors) == 1
    assert isinstance(second_errors[0], AppError)
    assert second_errors[0].code == "AIBRAIN_INSUFFICIENT_BALANCE"
    with factory() as db:
        wallet = db.get(ReasoningWallet, tenant_id)
        ledger_entries = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.tenant_id == tenant_id,
                    ReasoningLedgerEntry.entry_type == "reserve",
                )
            )
        )
        assert wallet.available_credits == Decimal("0")
        assert wallet.reserved_credits == Decimal("200")
        assert len(ledger_entries) == 1


def _seed_postgres_subscription(factory, *, total: int) -> tuple[str, str]:
    tenant_id = str(uuid4())
    now = datetime.now(UTC)
    with factory() as db:
        tenant = Tenant(
            id=tenant_id,
            slug=f"aibrain-topup-{uuid4().hex[:8]}",
            name="AIBRAIN Top-up Concurrency",
        )
        plan = Plan(
            code=f"aibrain-topup-{uuid4().hex[:8]}",
            name="AIBRAIN Top-up Concurrency",
            price_cents=0,
            period="monthly",
            quota_credits=total,
        )
        db.add_all([tenant, plan])
        db.flush()
        subscription = Subscription(
            tenant_id=tenant_id,
            plan_id=plan.id,
            status="active",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=30),
            quota_credits_total=total,
            quota_credits_used=0,
            quota_credits_reserved=0,
        )
        db.add(subscription)
        db.commit()
        return tenant_id, subscription.id


def test_postgres_concurrent_topups_cannot_overdraw_primary_quota(
    postgres_session_factory,
) -> None:
    factory = postgres_session_factory
    tenant_id, subscription_id = _seed_postgres_subscription(factory, total=100)
    start = threading.Event()
    done = [threading.Event(), threading.Event()]
    errors: list[BaseException] = []
    idempotency_keys = [uuid4(), uuid4()]

    def top_up(index: int) -> None:
        try:
            assert start.wait(timeout=5)
            with factory() as db:
                aibrain.top_up_reasoning_wallet(
                    db,
                    tenant_id=tenant_id,
                    amount=100,
                    idempotency_key=idempotency_keys[index],
                )
        except BaseException as exc:  # noqa: BLE001 - thread failures are evidence.
            errors.append(exc)
        finally:
            done[index].set()

    threads = [threading.Thread(target=top_up, args=(index,), daemon=True) for index in range(2)]
    for thread in threads:
        thread.start()
    start.set()
    for event_ in done:
        assert event_.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(errors) == 1
    assert isinstance(errors[0], AppError)
    assert errors[0].code == "TENANT_QUOTA_EXCEEDED"
    with factory() as db:
        subscription = db.get(Subscription, subscription_id)
        wallet = db.get(ReasoningWallet, tenant_id)
        ledger_entries = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(ReasoningLedgerEntry.tenant_id == tenant_id)
            )
        )
        assert subscription.quota_credits_used == 100
        assert wallet.available_credits == Decimal("100")
        assert wallet.reserved_credits == Decimal("0")
        assert len(ledger_entries) == 1


def test_postgres_concurrent_topup_replays_transfer_primary_quota_once(
    postgres_session_factory,
) -> None:
    factory = postgres_session_factory
    tenant_id, subscription_id = _seed_postgres_subscription(factory, total=200)
    idempotency_key = uuid4()
    start = threading.Event()
    done = [threading.Event(), threading.Event()]
    errors: list[BaseException] = []
    responses = []

    def top_up(index: int) -> None:
        try:
            assert start.wait(timeout=5)
            with factory() as db:
                responses.append(
                    aibrain.top_up_reasoning_wallet(
                        db,
                        tenant_id=tenant_id,
                        amount=100,
                        idempotency_key=idempotency_key,
                    )
                )
        except BaseException as exc:  # noqa: BLE001 - thread failures are evidence.
            errors.append(exc)
        finally:
            done[index].set()

    threads = [threading.Thread(target=top_up, args=(index,), daemon=True) for index in range(2)]
    for thread in threads:
        thread.start()
    start.set()
    for event_ in done:
        assert event_.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(responses) == 2
    assert responses[0] == responses[1]
    with factory() as db:
        subscription = db.get(Subscription, subscription_id)
        wallet = db.get(ReasoningWallet, tenant_id)
        topups = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.tenant_id == tenant_id,
                    ReasoningLedgerEntry.entry_type == "topup",
                )
            )
        )
        assert subscription.quota_credits_used == 100
        assert wallet.available_credits == Decimal("100")
        assert wallet.total_topup_credits == Decimal("100")
        assert len(topups) == 1


def test_postgres_aibrain_recovery_skips_a_message_locked_for_settlement(
    postgres_session_factory,
) -> None:
    from app.services import task_recovery

    factory = postgres_session_factory
    tenant_id = _seed_postgres_wallet(factory, available=Decimal("200"))
    now = datetime.now(UTC)
    with factory() as db:
        conversation = ChatConversation(
            tenant_id=tenant_id,
            title="Settlement race",
        )
        db.add(conversation)
        db.flush()
        message = ChatMessage(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            role="user",
            content="settling",
            attachments=[],
            tier="low",
            model="gpt-5.6-luna",
            status="pending",
            created_at=now - timedelta(minutes=31),
            updated_at=now - timedelta(minutes=31),
        )
        db.add(message)
        db.flush()
        reservation = aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=tenant_id,
            entry_type="reserve",
            amount_credits=Decimal("200"),
            chat_message_id=message.id,
            operation_key=f"reserve:{message.id}",
        )
        message.reserved_credits = reservation.ledger_entry.amount_credits
        message_id = message.id
        db.commit()

    settlement = factory()
    recovery_done = threading.Event()
    recovery_errors: list[BaseException] = []
    recovery_results = []
    try:
        message = aibrain._chat_message_for_update(
            settlement,
            tenant_id=tenant_id,
            message_id=message_id,
        )

        def recover() -> None:
            try:
                recovery_results.append(
                    task_recovery.recover_orphaned_image_queue_tasks(
                        session_factory=factory,
                        now=now,
                        stale_after_seconds=1800,
                        aibrain_stale_after_seconds=1800,
                    )
                )
            except BaseException as exc:  # noqa: BLE001 - thread failures are evidence.
                recovery_errors.append(exc)
            finally:
                recovery_done.set()

        recovery_thread = threading.Thread(target=recover, daemon=True)
        recovery_thread.start()
        assert recovery_done.wait(timeout=5)
        recovery_thread.join(timeout=5)
        assert recovery_errors == []
        assert recovery_results[0].aibrain_reservations == 0

        message.status = "completed"
        aibrain._apply_reasoning_wallet_change(
            settlement,
            tenant_id=tenant_id,
            entry_type="settle",
            amount_credits=Decimal("25"),
            reserved_credits=message.reserved_credits,
            chat_message_id=message.id,
            operation_key=f"settle:{message.id}",
        )
        settlement.commit()
    finally:
        settlement.rollback()
        settlement.close()

    with factory() as db:
        wallet = db.get(ReasoningWallet, tenant_id)
        message = db.get(ChatMessage, message_id)
        releases = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.tenant_id == tenant_id,
                    ReasoningLedgerEntry.entry_type == "release",
                )
            )
        )
        assert message.status == "completed"
        assert wallet.available_credits == Decimal("175")
        assert wallet.reserved_credits == Decimal("0")
        assert releases == []


class _DelayedChatProvider:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    async def chat(self, _payload: dict[str, object]) -> dict[str, object]:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("Delayed chat provider was not released by the test.")
        return {
            "content": "late provider response",
            "model": "gpt-5.6-luna",
            "prompt_tokens": 120,
            "completion_tokens": 40,
            "total_tokens": 160,
        }


def test_postgres_recovery_commits_before_a_delayed_provider_can_settle(
    postgres_session_factory,
    monkeypatch,
) -> None:
    from app.services import task_recovery

    factory = postgres_session_factory
    tenant_id = str(uuid4())
    with factory() as db:
        tenant = Tenant(
            id=tenant_id,
            slug=f"aibrain-late-provider-{uuid4().hex[:8]}",
            name="AIBRAIN Late Provider",
        )
        user = User(
            tenant_id=tenant_id,
            email=f"late-provider-{uuid4().hex[:8]}@example.com",
            password_hash="not-used-by-test",
        )
        conversation = ChatConversation(
            tenant_id=tenant_id,
            title="Late provider response",
        )
        db.add(tenant)
        db.flush()
        db.add_all([user, conversation])
        db.flush()
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=tenant_id,
            entry_type="topup",
            amount_credits=Decimal("200"),
            operation_key=f"seed:{tenant_id}",
        )
        user_id = user.id
        conversation_id = conversation.id
        db.commit()

    provider = _DelayedChatProvider()
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)
    send_errors: list[BaseException] = []

    def send_message() -> None:
        try:
            with factory() as db:
                user = db.get(User, user_id)
                asyncio.run(
                    aibrain.send_chat_message(
                        db,
                        user=user,
                        conversation_id=conversation_id,
                        content="Wait for recovery",
                        tier="low",
                        attachment_asset_ids=[],
                        storage=object(),
                    )
                )
        except BaseException as exc:  # noqa: BLE001 - thread failure is evidence.
            send_errors.append(exc)

    sender = threading.Thread(target=send_message, daemon=True)
    sender.start()
    assert provider.started.wait(timeout=5)

    recovery_result = task_recovery.recover_orphaned_image_queue_tasks(
        session_factory=factory,
        now=datetime.now(UTC) + timedelta(minutes=31),
        stale_after_seconds=1800,
        aibrain_stale_after_seconds=1800,
    )
    assert recovery_result.aibrain_reservations == 1
    with factory() as db:
        released_wallet = db.get(ReasoningWallet, tenant_id)
        released_balance = (
            released_wallet.available_credits,
            released_wallet.reserved_credits,
            released_wallet.total_spent_credits,
        )

    provider.release.set()
    sender.join(timeout=5)

    assert not sender.is_alive()
    assert len(send_errors) == 1
    assert isinstance(send_errors[0], AppError)
    assert send_errors[0].code == "AIBRAIN_REQUEST_EXPIRED"
    with factory() as db:
        wallet = db.get(ReasoningWallet, tenant_id)
        user_messages = list(
            db.scalars(
                select(ChatMessage).where(
                    ChatMessage.tenant_id == tenant_id,
                    ChatMessage.role == "user",
                )
            )
        )
        assistant_messages = list(
            db.scalars(
                select(ChatMessage).where(
                    ChatMessage.tenant_id == tenant_id,
                    ChatMessage.role == "assistant",
                )
            )
        )
        settled_usage = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == tenant_id,
                    UsageRecord.status == "settled",
                )
            )
        )
        settlements = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.tenant_id == tenant_id,
                    ReasoningLedgerEntry.entry_type == "settle",
                )
            )
        )
        assert len(user_messages) == 1
        assert user_messages[0].status == "failed"
        assert assistant_messages == []
        assert settled_usage == []
        assert settlements == []
        assert (
            wallet.available_credits,
            wallet.reserved_credits,
            wallet.total_spent_credits,
        ) == released_balance
