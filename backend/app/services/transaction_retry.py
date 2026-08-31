from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.orm import Session, sessionmaker

T = TypeVar("T")

_RETRYABLE_SQLSTATES = frozenset({"40P01", "40001"})
_MIN_RETRY_DELAY_SECONDS = 0.01
_MAX_RETRY_DELAY_SECONDS = 0.1


def _sqlstate(exc: BaseException) -> str | None:
    candidates = (exc, getattr(exc, "orig", None))
    for candidate in candidates:
        if candidate is None:
            continue
        value = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if isinstance(value, str):
            return value
    return None


def _detach_orm_result_before_commit(session: Session, result: T) -> T:
    state = sqlalchemy_inspect(result, raiseerr=False)
    if state is not None and getattr(state, "persistent", False):
        session.expunge(result)
    return result


def run_db_transaction_with_retry(
    session_factory: sessionmaker[Session],
    operation: Callable[[Session], T],
    *,
    max_attempts: int = 3,
) -> T:
    """Run one database-only transaction with bounded PostgreSQL retry."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least one")

    for attempt in range(1, max_attempts + 1):
        session = session_factory()
        try:
            result = operation(session)
            session.flush()
            result = _detach_orm_result_before_commit(session, result)
            session.commit()
        except Exception as exc:
            session.rollback()
            if _sqlstate(exc) not in _RETRYABLE_SQLSTATES or attempt >= max_attempts:
                raise
            upper_bound = min(
                _MAX_RETRY_DELAY_SECONDS,
                _MIN_RETRY_DELAY_SECONDS * (2**attempt),
            )
            time.sleep(random.uniform(_MIN_RETRY_DELAY_SECONDS, upper_bound))
        else:
            return result
        finally:
            session.close()

    raise AssertionError("unreachable")
