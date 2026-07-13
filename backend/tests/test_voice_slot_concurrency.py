from __future__ import annotations

import os
import threading
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker


def test_speaker_slot_postgres_lock_uses_a_stable_per_slot_advisory_key() -> None:
    from app.services import voice_slots

    class PostgresBind:
        class Dialect:
            name = "postgresql"

        dialect = Dialect()

    class CapturingSession:
        statements: list[tuple[str, dict[str, int]]] = []

        @staticmethod
        def get_bind():
            return PostgresBind()

        @classmethod
        def execute(cls, statement, params) -> None:
            cls.statements.append((str(statement), params))

    session = CapturingSession()
    voice_slots._lock_speaker_slot(session, "S_same")
    voice_slots._lock_speaker_slot(session, "S_same")
    voice_slots._lock_speaker_slot(session, "S_other")

    assert all("pg_advisory_xact_lock" in sql for sql, _ in session.statements)
    keys = [params["lock_id"] for _, params in session.statements]
    assert keys[0] == keys[1]
    assert keys[0] != keys[2]
    assert all(-(2**63) <= key < 2**63 for key in keys)


def test_speaker_slot_assignment_locks_the_global_speaker_id_before_scanning(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    from app.services import voice_slots

    lock_calls: list[str] = []
    monkeypatch.setattr(
        voice_slots,
        "_lock_speaker_slot",
        lambda db, speaker_id: lock_calls.append(speaker_id),
        raising=False,
    )

    with auth_db() as db:
        summary = voice_slots.assign_speaker_slot(
            db,
            tenant_slug="acme",
            speaker_id="S_concurrency_guard",
            apply=False,
            platform_speaker_ids=[],
        )

    assert summary.speaker_id == "S_concurrency_guard"
    assert lock_calls == ["S_concurrency_guard"]


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the real advisory-lock test.",
)
def test_postgres_concurrent_tenants_cannot_claim_the_same_speaker_id() -> None:
    from app.db.models import Base, ProviderConfig, Tenant
    from app.services.voice_slots import (
        SpeakerSlotAssignmentError,
        assign_speaker_slot,
    )

    engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_pre_ping=True)
    schema = f"voice_slot_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_engine = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(
        scoped_engine,
        tables=[Tenant.__table__, ProviderConfig.__table__],
    )
    Session = sessionmaker(bind=scoped_engine, autoflush=False, autocommit=False)
    first_prepared = threading.Event()
    release_first = threading.Event()
    second_done = threading.Event()
    outcomes: list[tuple[str, str]] = []
    first: threading.Thread | None = None
    second: threading.Thread | None = None

    try:
        with Session() as db:
            db.add_all(
                [
                    Tenant(id="tenant-one", slug="tenant-one", name="Tenant One"),
                    Tenant(id="tenant-two", slug="tenant-two", name="Tenant Two"),
                ]
            )
            db.commit()

        def claim_first() -> None:
            with Session() as db:
                assign_speaker_slot(
                    db,
                    tenant_slug="tenant-one",
                    speaker_id="S_global_unique",
                    apply=True,
                    platform_speaker_ids=[],
                )
                first_prepared.set()
                release_first.wait(timeout=5)
                db.commit()
                outcomes.append(("tenant-one", "assigned"))

        def claim_second() -> None:
            try:
                with Session() as db:
                    assign_speaker_slot(
                        db,
                        tenant_slug="tenant-two",
                        speaker_id="S_global_unique",
                        apply=True,
                        platform_speaker_ids=[],
                    )
                    db.commit()
                    outcomes.append(("tenant-two", "assigned"))
            except SpeakerSlotAssignmentError:
                outcomes.append(("tenant-two", "rejected"))
            finally:
                second_done.set()

        first = threading.Thread(target=claim_first, daemon=True)
        second = threading.Thread(target=claim_second, daemon=True)
        first.start()
        assert first_prepared.wait(timeout=5)
        second.start()
        assert not second_done.wait(timeout=0.25)
        release_first.set()
        first.join(timeout=5)
        second.join(timeout=5)

        assert sorted(outcomes) == [
            ("tenant-one", "assigned"),
            ("tenant-two", "rejected"),
        ]
        with Session() as db:
            configs = list(db.scalars(select(ProviderConfig)))
            assert len(configs) == 1
            assert configs[0].tenant_id == "tenant-one"
            assert configs[0].config == {"speaker_ids": ["S_global_unique"]}
    finally:
        release_first.set()
        if first is not None:
            first.join(timeout=5)
        if second is not None:
            second.join(timeout=5)
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
