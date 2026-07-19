"""Runtime guard for AIBRAIN wallet balances.

Mapped attributes and typed SQLAlchemy INSERT/UPDATE statements are protected here.
Arbitrary TextClause or native-driver SQL is opaque to SQLAlchemy's semantic events and
remains outside this guard. That boundary is accepted because application code has no
raw-SQL wallet mutation path; migrations and direct database administration are trusted,
separately reviewed operations.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from typing import ParamSpec, TypeVar

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.sql.dml import Insert, Update

_WALLET_MONETARY_FIELDS = (
    "available_credits",
    "reserved_credits",
    "total_topup_credits",
    "total_spent_credits",
)
_wallet_mutation_allowed: ContextVar[bool] = ContextVar(
    "reasoning_wallet_mutation_allowed",
    default=False,
)
_MUTATION_ERROR_MESSAGE = (
    "Reasoning wallet balances may only change inside the locked AIBRAIN wallet helper."
)
_installed_model: type[object] | None = None
_wallet_table_name: str | None = None
P = ParamSpec("P")
R = TypeVar("R")


class ReasoningWalletMutationError(RuntimeError):
    pass


def allow_reasoning_wallet_mutation(function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        token = _wallet_mutation_allowed.set(True)
        try:
            return function(*args, **kwargs)
        finally:
            _wallet_mutation_allowed.reset(token)

    return guarded


def install_reasoning_wallet_guard(wallet_model: type[object]) -> None:
    global _installed_model, _wallet_table_name
    if _installed_model is wallet_model:
        return
    if _installed_model is not None:  # pragma: no cover - models are configured once.
        raise RuntimeError("Reasoning wallet guard is already installed for another model.")
    for field_name in _WALLET_MONETARY_FIELDS:
        event.listen(
            getattr(wallet_model, field_name),
            "set",
            _guard_wallet_attribute_set,
            retval=True,
            active_history=True,
        )
    _wallet_table_name = str(wallet_model.__table__.name)
    event.listen(
        Engine,
        "before_execute",
        _guard_wallet_core_mutation,
        retval=True,
    )
    _installed_model = wallet_model


def _guard_wallet_attribute_set(
    _target: object,
    value: object,
    old_value: object,
    _initiator: object,
) -> object:
    if not _wallet_mutation_allowed.get() and value != old_value:
        raise ReasoningWalletMutationError(_MUTATION_ERROR_MESSAGE)
    return value


def _guard_wallet_core_mutation(
    _connection: object,
    statement: object,
    multiparams: object,
    params: object,
    _execution_options: object,
) -> tuple[object, object, object]:
    table = getattr(statement, "table", None)
    if (
        not _wallet_mutation_allowed.get()
        and isinstance(statement, Insert | Update)
        and getattr(table, "name", None) == _wallet_table_name
    ):
        raise ReasoningWalletMutationError(_MUTATION_ERROR_MESSAGE)
    return statement, multiparams, params
