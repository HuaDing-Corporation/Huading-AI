from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import Integer, String, create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.services.transaction_retry import run_db_transaction_with_retry


@dataclass
class FakeDbError(Exception):
    sqlstate: str


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.flushes = 0
        self.rollbacks = 0
        self.closes = 0

    def commit(self) -> None:
        self.commits += 1

    def flush(self) -> None:
        self.flushes += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1


class FakeFactory:
    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession()
        self.sessions.append(session)
        return session


class RetryProbeBase(DeclarativeBase):
    pass


class RetryProbe(RetryProbeBase):
    __tablename__ = "retry_probe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    value: Mapped[str] = mapped_column(String(32))


class RefreshFailsAfterCommitSession(Session):
    refresh_calls = 0

    def refresh(self, instance, attribute_names=None, with_for_update=None) -> None:
        type(self).refresh_calls += 1
        raise OperationalError("refresh", {}, FakeDbError("40001"))


@pytest.mark.parametrize("sqlstate", ["40P01", "40001"])
def test_retries_only_deadlock_and_serialization_with_fresh_sessions(
    monkeypatch, sqlstate
):
    factory = FakeFactory()
    sleeps: list[float] = []
    monkeypatch.setattr("app.services.transaction_retry.time.sleep", sleeps.append)
    monkeypatch.setattr("app.services.transaction_retry.random.uniform", lambda low, high: high)
    attempts = 0

    def operation(_session):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OperationalError("statement", {}, FakeDbError(sqlstate))
        return "ok"

    assert run_db_transaction_with_retry(factory, operation) == "ok"
    assert attempts == 3
    assert len(factory.sessions) == 3
    assert [session.rollbacks for session in factory.sessions] == [1, 1, 0]
    assert [session.closes for session in factory.sessions] == [1, 1, 1]
    assert [session.commits for session in factory.sessions] == [0, 0, 1]
    assert len(sleeps) == 2
    assert all(0 < delay <= 0.1 for delay in sleeps)


def test_retry_stops_after_three_total_attempts(monkeypatch):
    factory = FakeFactory()
    monkeypatch.setattr("app.services.transaction_retry.time.sleep", lambda _delay: None)

    def operation(_session):
        raise OperationalError("statement", {}, FakeDbError("40P01"))

    with pytest.raises(OperationalError):
        run_db_transaction_with_retry(factory, operation)

    assert len(factory.sessions) == 3
    assert all(session.rollbacks == 1 and session.closes == 1 for session in factory.sessions)


def test_non_retryable_sqlstate_is_raised_immediately(monkeypatch):
    factory = FakeFactory()
    monkeypatch.setattr(
        "app.services.transaction_retry.time.sleep",
        lambda _delay: pytest.fail("non-retryable errors must not sleep"),
    )

    def operation(_session):
        raise OperationalError("statement", {}, FakeDbError("23505"))

    with pytest.raises(OperationalError):
        run_db_transaction_with_retry(factory, operation)

    assert len(factory.sessions) == 1
    assert factory.sessions[0].rollbacks == 1
    assert factory.sessions[0].closes == 1


def test_invalid_max_attempts_is_rejected_before_opening_session():
    factory = FakeFactory()
    with pytest.raises(ValueError):
        run_db_transaction_with_retry(factory, lambda _session: None, max_attempts=0)
    assert factory.sessions == []


def test_no_fallible_database_work_or_retry_occurs_after_commit():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RetryProbeBase.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        class_=RefreshFailsAfterCommitSession,
        autoflush=False,
        expire_on_commit=True,
    )
    RefreshFailsAfterCommitSession.refresh_calls = 0
    attempts = 0

    def operation(db: Session) -> RetryProbe:
        nonlocal attempts
        attempts += 1
        probe = RetryProbe(value="committed-once")
        db.add(probe)
        return probe

    probe = run_db_transaction_with_retry(factory, operation)

    assert attempts == 1
    assert RefreshFailsAfterCommitSession.refresh_calls == 0
    assert probe.id is not None
    assert probe.value == "committed-once"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(RetryProbe)) == 1
    engine.dispose()
