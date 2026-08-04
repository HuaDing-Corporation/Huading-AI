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

from app.core.config import settings
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


def test_settlement_above_reservation_requires_explicit_overdraft_authorization(
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
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="reserve",
            amount_credits=Decimal("10"),
        )

        with pytest.raises(
            RuntimeError,
            match="exceeds its reservation without overdraft authorization",
        ):
            aibrain._apply_reasoning_wallet_change(
                db,
                tenant_id=auth_context["tenant_id"],
                entry_type="settle",
                amount_credits=Decimal("11"),
                reserved_credits=Decimal("10"),
            )


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


class _ExposureBlockingChatProvider:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self.calls: list[dict[str, object]] = []
        self.release = threading.Event()

    async def chat(self, payload: dict[str, object]) -> dict[str, object]:
        with self._condition:
            self.calls.append(payload)
            call_count = len(self.calls)
            self._condition.notify_all()
        if call_count > 2:
            raise AssertionError("A third provider call bypassed the exposure gate.")
        if not self.release.wait(timeout=5):
            raise TimeoutError("Exposure test provider was not released.")
        return {
            "content": "authorized provider response",
            "model": "gpt-5.6-sol",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        }

    def wait_for_calls(self, expected: int, *, timeout: float = 5) -> bool:
        deadline = datetime.now(UTC).timestamp() + timeout
        with self._condition:
            while len(self.calls) < expected:
                remaining = deadline - datetime.now(UTC).timestamp()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
        return True


def test_postgres_inflight_exposure_authorization_is_atomic_before_provider(
    postgres_session_factory,
    monkeypatch,
) -> None:
    factory = postgres_session_factory
    tenant_id = str(uuid4())
    with factory() as db:
        tenant = Tenant(
            id=tenant_id,
            slug=f"aibrain-exposure-{uuid4().hex[:8]}",
            name="AIBRAIN Exposure",
        )
        user = User(
            tenant_id=tenant_id,
            email=f"exposure-{uuid4().hex[:8]}@example.com",
            password_hash="not-used-by-test",
        )
        db.add(tenant)
        db.flush([tenant])
        db.add(user)
        conversations = [
            ChatConversation(
                tenant_id=tenant_id,
                title=f"Exposure request {index}",
            )
            for index in range(3)
        ]
        db.add_all(conversations)
        db.flush([user, *conversations])
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=tenant_id,
            entry_type="topup",
            amount_credits=Decimal("1000"),
            operation_key=f"seed:{tenant_id}",
        )
        user_id = user.id
        conversation_ids = [conversation.id for conversation in conversations]
        db.commit()

    provider = _ExposureBlockingChatProvider()
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(settings, "engine_aibrain_max_prompt_tokens", 1000)
    monkeypatch.setattr(settings, "engine_aibrain_inflight_exposure_multiplier", 2)
    original_exposure_snapshot = aibrain._current_inflight_exposure
    original_wallet_change = aibrain._apply_reasoning_wallet_change
    contender_entry_barrier = threading.Barrier(2)
    synchronize_contenders = threading.Event()
    first_stale_snapshot = threading.Event()
    second_stale_snapshot = threading.Event()
    release_stale_snapshot = threading.Event()
    stale_snapshot_lock = threading.Lock()
    stale_snapshot_count = 0

    def synchronized_wallet_change(*args, **kwargs):
        if (
            synchronize_contenders.is_set()
            and kwargs.get("entry_type") == "reserve"
            and kwargs.get("in_flight_exposure_credits") is not None
        ):
            contender_entry_barrier.wait(timeout=5)
        return original_wallet_change(*args, **kwargs)

    def synchronized_exposure_snapshot(*args, **kwargs):
        nonlocal stale_snapshot_count
        snapshot = original_exposure_snapshot(*args, **kwargs)
        if synchronize_contenders.is_set() and snapshot.requests == 1:
            with stale_snapshot_lock:
                stale_snapshot_count += 1
                snapshot_number = stale_snapshot_count
            if snapshot_number == 1:
                first_stale_snapshot.set()
            else:
                second_stale_snapshot.set()
            assert release_stale_snapshot.wait(timeout=5)
        return snapshot

    monkeypatch.setattr(
        aibrain,
        "_apply_reasoning_wallet_change",
        synchronized_wallet_change,
    )
    monkeypatch.setattr(
        aibrain,
        "_current_inflight_exposure",
        synchronized_exposure_snapshot,
    )
    rejected = threading.Event()
    results: list[object] = []
    errors: list[BaseException] = []

    def send(index: int) -> None:
        try:
            with factory() as db:
                user = db.get(User, user_id)
                results.append(
                    asyncio.run(
                        aibrain.send_chat_message(
                            db,
                            user=user,
                            conversation_id=conversation_ids[index],
                            content=f"Concurrent exposure request {index}",
                            tier="high",
                            attachment_asset_ids=[],
                            storage=object(),
                        )
                    )
                )
        except BaseException as exc:  # noqa: BLE001 - thread failures are evidence.
            errors.append(exc)
            rejected.set()

    first = threading.Thread(target=send, args=(0,))
    first.start()
    assert provider.wait_for_calls(1)

    contenders = [
        threading.Thread(
            target=send,
            args=(index,),
            daemon=True,
        )
        for index in (1, 2)
    ]
    synchronize_contenders.set()
    for thread in contenders:
        thread.start()

    assert first_stale_snapshot.wait(timeout=5)
    stale_snapshot_raced = second_stale_snapshot.wait(timeout=1)
    release_stale_snapshot.set()
    assert provider.wait_for_calls(2)
    assert rejected.wait(timeout=5)
    assert len(provider.calls) == 2
    provider.release.set()
    first.join(timeout=5)
    for thread in contenders:
        thread.join(timeout=5)

    assert not first.is_alive()
    assert all(not thread.is_alive() for thread in contenders)
    assert len(results) == 2
    assert len(errors) == 1
    assert stale_snapshot_raced is False
    assert isinstance(errors[0], AppError)
    assert errors[0].code == "AIBRAIN_INFLIGHT_EXPOSURE_LIMIT"
    assert errors[0].status_code == 402
    assert errors[0].detail["in_flight_requests"] == 2
    assert errors[0].detail["retryable"] is True

    with factory() as db:
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
        usage_records = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == tenant_id,
                    UsageRecord.capability == "chat",
                )
            )
        )
        assert len(user_messages) == 2
        assert all(message.status == "completed" for message in user_messages)
        assert len(assistant_messages) == 2
        assert len(usage_records) == 2
        assert all(record.status == "settled" for record in usage_records)


class _ThreadBackedCancellationProvider:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self.calls: list[dict[str, object]] = []
        self.active_threads = 0
        self.release = threading.Event()

    async def chat(self, payload: dict[str, object]) -> dict[str, object]:
        return await asyncio.to_thread(self._chat_sync, payload)

    def _chat_sync(self, payload: dict[str, object]) -> dict[str, object]:
        with self._condition:
            self.calls.append(payload)
            self.active_threads += 1
            call_count = len(self.calls)
            self._condition.notify_all()
        try:
            if call_count > 2:
                raise AssertionError("Cancellation released live provider exposure.")
            if not self.release.wait(timeout=5):
                raise TimeoutError("Cancellation test provider was not released.")
            return {
                "content": "provider work completed after caller cancellation",
                "model": "gpt-5.6-sol",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            }
        finally:
            with self._condition:
                self.active_threads -= 1
                self._condition.notify_all()

    def wait_for_calls(self, expected: int, *, timeout: float = 5) -> bool:
        deadline = datetime.now(UTC).timestamp() + timeout
        with self._condition:
            while len(self.calls) < expected:
                remaining = deadline - datetime.now(UTC).timestamp()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
        return True


def test_postgres_external_cancellation_keeps_live_provider_exposure_authorized(
    postgres_session_factory,
    monkeypatch,
) -> None:
    factory = postgres_session_factory
    tenant_id = str(uuid4())
    with factory() as db:
        tenant = Tenant(
            id=tenant_id,
            slug=f"aibrain-cancel-exposure-{uuid4().hex[:8]}",
            name="AIBRAIN Cancellation Exposure",
        )
        user = User(
            tenant_id=tenant_id,
            email=f"cancel-exposure-{uuid4().hex[:8]}@example.com",
            password_hash="not-used-by-test",
        )
        db.add(tenant)
        db.flush([tenant])
        db.add(user)
        conversations = [
            ChatConversation(
                tenant_id=tenant_id,
                title=f"Cancellation exposure {index}",
            )
            for index in range(3)
        ]
        db.add_all(conversations)
        db.flush([user, *conversations])
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=tenant_id,
            entry_type="topup",
            amount_credits=Decimal("100"),
            operation_key=f"seed:{tenant_id}",
        )
        user_id = user.id
        conversation_ids = [conversation.id for conversation in conversations]
        db.commit()

    provider = _ThreadBackedCancellationProvider()
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(settings, "engine_aibrain_max_prompt_tokens", 1000)
    monkeypatch.setattr(settings, "engine_aibrain_max_completion_tokens", 1)
    monkeypatch.setattr(settings, "engine_aibrain_inflight_exposure_multiplier", 2)
    monkeypatch.setattr(aibrain, "_inflight_heartbeat_interval_seconds", lambda: 0.05)

    async def scenario() -> list[BaseException]:
        sessions = [factory(), factory()]
        tasks: list[asyncio.Task[object]] = []
        outcomes: list[BaseException] = []
        try:
            for index, db in enumerate(sessions):
                user = db.get(User, user_id)
                tasks.append(
                    asyncio.create_task(
                        aibrain.send_chat_message(
                            db,
                            user=user,
                            conversation_id=conversation_ids[index],
                            content=str(index),
                            tier="high",
                            attachment_asset_ids=[],
                            storage=object(),
                        )
                    )
                )
            assert await asyncio.to_thread(provider.wait_for_calls, 2)

            heartbeat_cutoff = datetime.now(UTC) - timedelta(minutes=5)
            with factory() as db:
                db.execute(
                    update(ChatMessage)
                    .where(
                        ChatMessage.tenant_id == tenant_id,
                        ChatMessage.role == "user",
                        ChatMessage.status == "pending",
                    )
                    .values(updated_at=datetime.now(UTC) - timedelta(hours=1))
                )
                db.commit()
            for _attempt in range(100):
                await asyncio.sleep(0.02)
                with factory() as db:
                    heartbeat_times = list(
                        db.scalars(
                            select(ChatMessage.updated_at).where(
                                ChatMessage.tenant_id == tenant_id,
                                ChatMessage.role == "user",
                                ChatMessage.status == "pending",
                            )
                        )
                    )
                if len(heartbeat_times) == 2 and all(
                    updated_at > heartbeat_cutoff for updated_at in heartbeat_times
                ):
                    break
            assert len(heartbeat_times) == 2
            assert all(updated_at > heartbeat_cutoff for updated_at in heartbeat_times)
            with factory() as db:
                recovered = aibrain.recover_stale_reasoning_reservations(
                    db,
                    cutoff=heartbeat_cutoff,
                    recovered_at=datetime.now(UTC),
                )
                assert recovered == 0
                db.rollback()

            for task in tasks:
                task.cancel()
            await asyncio.sleep(0)
            assert all(not task.done() for task in tasks)
            assert provider.active_threads == 2

            # Parent cancellation is absorbed until the shielded paid work finishes.
            # Prove the shared native scheduler keeps both leases alive during that
            # post-cancel window, not only before cancellation is requested.
            post_cancel_cutoff = datetime.now(UTC) - timedelta(minutes=5)
            with factory() as db:
                db.execute(
                    update(ChatMessage)
                    .where(
                        ChatMessage.tenant_id == tenant_id,
                        ChatMessage.role == "user",
                        ChatMessage.status == "pending",
                    )
                    .values(updated_at=datetime.now(UTC) - timedelta(hours=1))
                )
                db.commit()
            post_cancel_heartbeat_times: list[datetime] = []
            for _attempt in range(100):
                await asyncio.sleep(0.02)
                with factory() as db:
                    post_cancel_heartbeat_times = list(
                        db.scalars(
                            select(ChatMessage.updated_at).where(
                                ChatMessage.tenant_id == tenant_id,
                                ChatMessage.role == "user",
                                ChatMessage.status == "pending",
                            )
                        )
                    )
                if len(post_cancel_heartbeat_times) == 2 and all(
                    updated_at > post_cancel_cutoff
                    for updated_at in post_cancel_heartbeat_times
                ):
                    break
            assert len(post_cancel_heartbeat_times) == 2
            assert all(
                updated_at > post_cancel_cutoff
                for updated_at in post_cancel_heartbeat_times
            )
            with factory() as db:
                recovered = aibrain.recover_stale_reasoning_reservations(
                    db,
                    cutoff=post_cancel_cutoff,
                    recovered_at=datetime.now(UTC),
                )
                assert recovered == 0
                db.rollback()

            with factory() as db:
                user = db.get(User, user_id)
                with pytest.raises(AppError) as blocked:
                    await aibrain.send_chat_message(
                        db,
                        user=user,
                        conversation_id=conversation_ids[2],
                        content="A third request must remain blocked",
                        tier="low",
                        attachment_asset_ids=[],
                        storage=object(),
                    )
                assert blocked.value.code == "AIBRAIN_INFLIGHT_EXPOSURE_LIMIT"
                assert blocked.value.detail["in_flight_requests"] == 2
                db.rollback()
            assert len(provider.calls) == 2
        finally:
            provider.release.set()
            if tasks:
                gathered = await asyncio.gather(*tasks, return_exceptions=True)
                outcomes = [item for item in gathered if isinstance(item, BaseException)]
            for db in sessions:
                db.close()
        return outcomes

    outcomes = asyncio.run(scenario())
    assert len(outcomes) == 2
    assert all(isinstance(item, asyncio.CancelledError) for item in outcomes)

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
        assert wallet.available_credits == Decimal("99.921600")
        assert wallet.reserved_credits == Decimal("0")
        assert wallet.total_spent_credits == Decimal("0.078400")
        assert len(user_messages) == 2
        assert all(message.status == "completed" for message in user_messages)
        assert len(assistant_messages) == 2


class _ImmediateChatProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def chat(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(payload)
        return {
            "content": "provider completed before settlement blocked",
            "model": "gpt-5.6-sol",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        }


def test_postgres_heartbeat_survives_blocked_post_provider_settlement(
    postgres_session_factory,
    monkeypatch,
) -> None:
    factory = postgres_session_factory
    tenant_id = str(uuid4())
    with factory() as db:
        tenant = Tenant(
            id=tenant_id,
            slug=f"aibrain-settlement-heartbeat-{uuid4().hex[:8]}",
            name="AIBRAIN Settlement Heartbeat",
        )
        user = User(
            tenant_id=tenant_id,
            email=f"settlement-heartbeat-{uuid4().hex[:8]}@example.com",
            password_hash="not-used-by-test",
        )
        conversation = ChatConversation(
            tenant_id=tenant_id,
            title="Settlement heartbeat",
        )
        db.add(tenant)
        db.flush([tenant])
        db.add_all([user, conversation])
        db.flush([user, conversation])
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=tenant_id,
            entry_type="topup",
            amount_credits=Decimal("100"),
            operation_key=f"seed:{tenant_id}",
        )
        user_id = user.id
        conversation_id = conversation.id
        db.commit()

    provider = _ImmediateChatProvider()
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(settings, "engine_aibrain_max_prompt_tokens", 1000)
    monkeypatch.setattr(settings, "engine_aibrain_max_completion_tokens", 1)
    monkeypatch.setattr(aibrain, "_inflight_heartbeat_interval_seconds", lambda: 0.05)
    original_provider_usage_cost = aibrain._provider_usage_cost
    settlement_blocked = threading.Event()
    release_settlement = threading.Event()

    def blocking_provider_usage_cost(*args, **kwargs):
        settlement_blocked.set()
        assert release_settlement.wait(timeout=5)
        return original_provider_usage_cost(*args, **kwargs)

    monkeypatch.setattr(aibrain, "_provider_usage_cost", blocking_provider_usage_cost)
    results: list[object] = []
    errors: list[BaseException] = []

    def send() -> None:
        try:
            with factory() as db:
                user = db.get(User, user_id)
                results.append(
                    asyncio.run(
                        aibrain.send_chat_message(
                            db,
                            user=user,
                            conversation_id=conversation_id,
                            content="Keep the lease alive through settlement",
                            tier="high",
                            attachment_asset_ids=[],
                            storage=object(),
                        )
                    )
                )
        except BaseException as exc:  # noqa: BLE001 - thread failures are evidence.
            errors.append(exc)

    sender = threading.Thread(target=send, daemon=True)
    sender.start()
    try:
        assert settlement_blocked.wait(timeout=5)
        heartbeat_cutoff = datetime.now(UTC) - timedelta(minutes=5)
        with factory() as db:
            db.execute(
                update(ChatMessage)
                .where(
                    ChatMessage.tenant_id == tenant_id,
                    ChatMessage.role == "user",
                    ChatMessage.status == "pending",
                )
                .values(updated_at=datetime.now(UTC) - timedelta(hours=1))
            )
            db.commit()

        heartbeat_times: list[datetime] = []
        for _attempt in range(100):
            threading.Event().wait(0.02)
            with factory() as db:
                heartbeat_times = list(
                    db.scalars(
                        select(ChatMessage.updated_at).where(
                            ChatMessage.tenant_id == tenant_id,
                            ChatMessage.role == "user",
                            ChatMessage.status == "pending",
                        )
                    )
                )
            if len(heartbeat_times) == 1 and heartbeat_times[0] > heartbeat_cutoff:
                break
        assert len(heartbeat_times) == 1
        assert heartbeat_times[0] > heartbeat_cutoff
        with factory() as db:
            recovered = aibrain.recover_stale_reasoning_reservations(
                db,
                cutoff=heartbeat_cutoff,
                recovered_at=datetime.now(UTC),
            )
            assert recovered == 0
            db.rollback()
    finally:
        release_settlement.set()
        sender.join(timeout=5)

    assert not sender.is_alive()
    assert errors == []
    assert len(results) == 1
    assert len(provider.calls) == 1
    with factory() as db:
        wallet = db.get(ReasoningWallet, tenant_id)
        user_message = db.scalar(
            select(ChatMessage).where(
                ChatMessage.tenant_id == tenant_id,
                ChatMessage.role == "user",
            )
        )
        assistant_message = db.scalar(
            select(ChatMessage).where(
                ChatMessage.tenant_id == tenant_id,
                ChatMessage.role == "assistant",
            )
        )
        assert wallet.reserved_credits == Decimal("0")
        assert user_message.status == "completed"
        assert assistant_message.status == "completed"


def test_postgres_locked_heartbeat_rows_do_not_starve_unlocked_lease(
    postgres_session_factory,
    monkeypatch,
) -> None:
    factory = postgres_session_factory
    tenant_id = str(uuid4())
    stale_timestamp = datetime.now(UTC) - timedelta(hours=1)
    cutoff = datetime.now(UTC) - timedelta(minutes=5)
    with factory() as db:
        tenant = Tenant(
            id=tenant_id,
            slug=f"heartbeat-lock-timeout-{uuid4().hex[:8]}",
            name="Heartbeat Lock Timeout",
        )
        db.add(tenant)
        db.flush([tenant])
        conversations = [
            ChatConversation(
                tenant_id=tenant_id,
                title=f"Heartbeat lock timeout {index}",
            )
            for index in range(5)
        ]
        db.add_all(conversations)
        db.flush(conversations)
        messages = [
            ChatMessage(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                role="user",
                content=f"Heartbeat lock timeout {index}",
                attachments=[],
                tier="high",
                model="gpt-5.6-sol",
                status="pending",
                updated_at=stale_timestamp,
            )
            for index, conversation in enumerate(conversations)
        ]
        db.add_all(messages)
        db.flush(messages)
        message_ids = [message.id for message in messages]
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=tenant_id,
            entry_type="topup",
            amount_credits=Decimal("10"),
            operation_key=f"seed:{tenant_id}",
        )
        live_reservation = aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=tenant_id,
            entry_type="reserve",
            amount_credits=Decimal("1"),
            chat_message_id=messages[4].id,
            operation_key=f"reserve:{messages[4].id}",
        ).ledger_entry.amount_credits
        messages[4].reserved_credits = live_reservation
        bind = db.get_bind()
        db.commit()
        db.execute(
            update(ChatMessage)
            .where(ChatMessage.id.in_(message_ids))
            .values(updated_at=stale_timestamp)
        )
        db.commit()

    locked_message_ids = message_ids[:4]
    unlocked_message_id = message_ids[4]
    lock_sessions = []
    scheduler = aibrain._InflightHeartbeatScheduler(worker_count=4, queue_capacity=5)
    attempted = threading.Condition()
    attempted_message_ids: set[str] = set()
    original_touch = aibrain._touch_pending_chat_message

    def observed_touch(bind, *, tenant_id: str, message_id: str) -> bool:
        with attempted:
            attempted_message_ids.add(message_id)
            attempted.notify_all()
        return original_touch(
            bind,
            tenant_id=tenant_id,
            message_id=message_id,
        )

    monkeypatch.setattr(aibrain, "_touch_pending_chat_message", observed_touch)
    monkeypatch.setattr(
        aibrain,
        "_INFLIGHT_HEARTBEAT_LOCK_TIMEOUT_MILLISECONDS",
        250,
    )
    monkeypatch.setattr(
        aibrain,
        "_INFLIGHT_HEARTBEAT_STATEMENT_TIMEOUT_MILLISECONDS",
        1_000,
    )
    entries: list[aibrain._InflightHeartbeatEntry] = []
    try:
        for message_id in locked_message_ids:
            lock_db = factory()
            lock_db.scalar(
                select(ChatMessage)
                .where(ChatMessage.id == message_id)
                .with_for_update()
            )
            lock_sessions.append(lock_db)

        for message_id in locked_message_ids:
            entries.append(
                scheduler.register(
                    bind,
                    tenant_id=tenant_id,
                    message_id=message_id,
                    interval_seconds=0.01,
                )
            )
        deadline = datetime.now(UTC).timestamp() + 5
        with attempted:
            while not set(locked_message_ids).issubset(attempted_message_ids):
                remaining = deadline - datetime.now(UTC).timestamp()
                assert remaining > 0
                attempted.wait(timeout=remaining)

        unlocked_entry = scheduler.register(
            bind,
            tenant_id=tenant_id,
            message_id=unlocked_message_id,
            interval_seconds=0.01,
        )
        entries.append(unlocked_entry)

        refreshed_at = stale_timestamp
        for _attempt in range(100):
            threading.Event().wait(0.02)
            with factory() as db:
                refreshed_at = db.scalar(
                    select(ChatMessage.updated_at).where(
                        ChatMessage.id == unlocked_message_id
                    )
                )
            if refreshed_at > cutoff:
                break

        assert refreshed_at > cutoff
        assert unlocked_message_id in attempted_message_ids
        with factory() as db:
            locked_timestamps = list(
                db.scalars(
                    select(ChatMessage.updated_at).where(
                        ChatMessage.id.in_(locked_message_ids)
                    )
                )
            )
        assert locked_timestamps == [stale_timestamp] * 4
    finally:
        for entry in entries:
            scheduler.unregister(entry)
        scheduler.shutdown(timeout=2)
        for lock_db in lock_sessions:
            lock_db.rollback()
            lock_db.close()

    with factory() as db:
        db.execute(
            update(ChatMessage)
            .where(ChatMessage.id.in_(locked_message_ids))
            .values(status="completed", updated_at=datetime.now(UTC))
        )
        db.commit()
        recovered = aibrain.recover_stale_reasoning_reservations(
            db,
            cutoff=cutoff,
            recovered_at=datetime.now(UTC),
        )
        assert recovered == 0
        db.rollback()
        wallet = db.get(ReasoningWallet, tenant_id)
        live_message = db.get(ChatMessage, unlocked_message_id)
        assert wallet.reserved_credits == Decimal("1")
        assert live_message.status == "pending"
        assert live_message.reserved_credits == Decimal("1")
        live_message.status = "failed"
        live_message.error_code = "AIBRAIN_RESERVATION_EXPIRED"
        live_message.updated_at = datetime.now(UTC)
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=tenant_id,
            entry_type="release",
            amount_credits=live_message.reserved_credits,
            reserved_credits=live_message.reserved_credits,
            chat_message_id=live_message.id,
            operation_key=f"release:{live_message.id}",
            details={"test_cleanup": True},
        )
        db.commit()
        db.refresh(wallet)
        assert wallet.reserved_credits == Decimal("0")


class _SequencedOverdraftChatProvider:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self.calls: list[dict[str, object]] = []
        self.release = [threading.Event(), threading.Event()]

    async def chat(self, payload: dict[str, object]) -> dict[str, object]:
        with self._condition:
            call_index = len(self.calls)
            self.calls.append(payload)
            self._condition.notify_all()
        if call_index >= len(self.release):
            raise AssertionError("An unauthorized request reached the provider.")
        if not self.release[call_index].wait(timeout=5):
            raise TimeoutError("Sequenced overdraft provider was not released.")
        return {
            "content": f"authorized overdraft response {call_index}",
            "model": "gpt-5.6-sol",
            "prompt_tokens": 500,
            "completion_tokens": 1,
            "total_tokens": 501,
        }

    def wait_for_calls(self, expected: int, *, timeout: float = 5) -> bool:
        deadline = datetime.now(UTC).timestamp() + timeout
        with self._condition:
            while len(self.calls) < expected:
                remaining = deadline - datetime.now(UTC).timestamp()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
        return True


def test_postgres_pre_authorized_requests_settle_after_first_overdraft(
    postgres_session_factory,
    monkeypatch,
) -> None:
    factory = postgres_session_factory
    tenant_id = str(uuid4())
    with factory() as db:
        tenant = Tenant(
            id=tenant_id,
            slug=f"aibrain-authorized-debt-{uuid4().hex[:8]}",
            name="AIBRAIN Authorized Debt",
        )
        user = User(
            tenant_id=tenant_id,
            email=f"authorized-debt-{uuid4().hex[:8]}@example.com",
            password_hash="not-used-by-test",
        )
        db.add(tenant)
        db.flush([tenant])
        db.add(user)
        conversations = [
            ChatConversation(
                tenant_id=tenant_id,
                title=f"Authorized debt {index}",
            )
            for index in range(3)
        ]
        db.add_all(conversations)
        db.flush([user, *conversations])
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=tenant_id,
            entry_type="topup",
            amount_credits=Decimal("1"),
            operation_key=f"seed:{tenant_id}",
        )
        user_id = user.id
        conversation_ids = [conversation.id for conversation in conversations]
        db.commit()

    provider = _SequencedOverdraftChatProvider()
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(settings, "engine_aibrain_max_prompt_tokens", 1000)
    monkeypatch.setattr(settings, "engine_aibrain_max_completion_tokens", 1)
    monkeypatch.setattr(settings, "engine_aibrain_inflight_exposure_multiplier", 2)
    completed = threading.Event()
    results: list[object] = []
    errors: list[BaseException] = []

    def send(index: int) -> None:
        try:
            with factory() as db:
                user = db.get(User, user_id)
                results.append(
                    asyncio.run(
                        aibrain.send_chat_message(
                            db,
                            user=user,
                            conversation_id=conversation_ids[index],
                            # Keep both real high-tier reservations below the seeded
                            # 1-credit balance, while the provider's 500-token usage
                            # still drives the first settlement into overdraft.
                            content=str(index),
                            tier="high",
                            attachment_asset_ids=[],
                            storage=object(),
                        )
                    )
                )
                completed.set()
        except BaseException as exc:  # noqa: BLE001 - thread failures are evidence.
            errors.append(exc)

    senders = [threading.Thread(target=send, args=(index,), daemon=True) for index in (0, 1)]
    for sender in senders:
        sender.start()
    assert provider.wait_for_calls(2)

    provider.release[0].set()
    assert completed.wait(timeout=5)
    with factory() as db:
        wallet_after_first = db.get(ReasoningWallet, tenant_id)
        assert wallet_after_first.available_credits < 0
        assert wallet_after_first.reserved_credits > 0

    provider.release[1].set()
    for sender in senders:
        sender.join(timeout=5)

    assert all(not sender.is_alive() for sender in senders)
    assert errors == []
    assert len(results) == 2
    assert len(provider.calls) == 2

    with factory() as db:
        user = db.get(User, user_id)
        with pytest.raises(AppError) as blocked:
            asyncio.run(
                aibrain.send_chat_message(
                    db,
                    user=user,
                    conversation_id=conversation_ids[2],
                    content="Block new work after the authorized debt settles",
                    tier="low",
                    attachment_asset_ids=[],
                    storage=object(),
                )
            )
        assert blocked.value.code == "AIBRAIN_OUTSTANDING_BALANCE"
        assert len(provider.calls) == 2
        db.rollback()

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
        assert wallet.available_credits == Decimal("-4.667200")
        assert wallet.reserved_credits == Decimal("0")
        assert wallet.total_spent_credits == Decimal("5.667200")
        assert len(user_messages) == 2
        assert all(message.status == "completed" for message in user_messages)
        assert len(assistant_messages) == 2


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
