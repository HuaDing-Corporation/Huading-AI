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
def test_postgres_admin_path_uses_advisory_lock_before_existing_config_rows(
    monkeypatch,
) -> None:
    from app.db.models import AdminAuditLog, Base, ProviderConfig, Tenant, User
    from app.services import admin_console
    from app.services.voice_slots import SpeakerSlotAssignmentError

    engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_pre_ping=True)
    schema = f"voice_slot_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_engine = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(
        scoped_engine,
        tables=[
            Tenant.__table__,
            User.__table__,
            ProviderConfig.__table__,
            AdminAuditLog.__table__,
        ],
    )
    Session = sessionmaker(bind=scoped_engine, autoflush=False, autocommit=False)
    start_together = threading.Barrier(2)
    outcomes: list[tuple[str, str]] = []
    threads: list[threading.Thread] = []
    real_assign = admin_console.assign_speaker_slot

    def synchronized_assign(*args, **kwargs):
        start_together.wait(timeout=5)
        return real_assign(*args, **kwargs)

    monkeypatch.setattr(admin_console, "assign_speaker_slot", synchronized_assign)

    try:
        with Session() as db:
            platform = Tenant(id="platform", slug="platform", name="Platform")
            tenant_one = Tenant(id="tenant-one", slug="tenant-one", name="Tenant One")
            tenant_two = Tenant(id="tenant-two", slug="tenant-two", name="Tenant Two")
            db.add_all([platform, tenant_one, tenant_two])
            db.flush()
            db.add_all(
                [
                    User(
                        id="platform-user",
                        tenant_id=platform.id,
                        email="platform@example.test",
                        password_hash="not-used",
                        role="admin",
                    ),
                    ProviderConfig(
                        tenant_id=tenant_one.id,
                        capability="voice_clone",
                        provider="doubao-voice-clone",
                        config={"speaker_ids": []},
                        is_active=True,
                    ),
                    ProviderConfig(
                        tenant_id=tenant_two.id,
                        capability="voice_clone",
                        provider="doubao-voice-clone",
                        config={"speaker_ids": []},
                        is_active=True,
                    ),
                ]
            )
            db.commit()

        def claim(tenant_id: str) -> None:
            try:
                with Session() as db:
                    actor = db.get(User, "platform-user")
                    admin_console.assign_tenant_voice_slot(
                        db,
                        actor=actor,
                        tenant_id=tenant_id,
                        speaker_id="S_global_unique",
                        reason="concurrency test",
                    )
                    db.commit()
                    outcomes.append((tenant_id, "assigned"))
            except SpeakerSlotAssignmentError:
                outcomes.append((tenant_id, "rejected"))
            except Exception as exc:  # noqa: BLE001 - a deadlock must fail with its real type.
                outcomes.append((tenant_id, f"unexpected:{type(exc).__name__}"))

        threads = [
            threading.Thread(target=claim, args=(tenant_id,), daemon=True)
            for tenant_id in ("tenant-one", "tenant-two")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert all(not thread.is_alive() for thread in threads)
        assert sorted(status for _, status in outcomes) == ["assigned", "rejected"]
        with Session() as db:
            configs = list(
                db.scalars(
                    select(ProviderConfig)
                    .where(ProviderConfig.tenant_id.is_not(None))
                    .order_by(ProviderConfig.tenant_id)
                )
            )
            assert len(configs) == 2
            assigned = [
                config.tenant_id
                for config in configs
                if config.config == {"speaker_ids": ["S_global_unique"]}
            ]
            assert len(assigned) == 1
    finally:
        try:
            start_together.abort()
        except threading.BrokenBarrierError:
            pass
        for thread in threads:
            thread.join(timeout=5)
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
