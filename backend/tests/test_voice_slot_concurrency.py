from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import AppError


def _wait_for_postgres_advisory_blocker(
    engine,
    *,
    blocked_backend_pid: int,
    blocking_backend_pid: int,
    timeout: float = 5.0,
) -> bool:
    deadline = time.monotonic() + timeout
    with engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        while time.monotonic() < deadline:
            waiting = connection.scalar(
                text(
                    """
                    SELECT
                        :blocking_backend_pid = ANY(
                            pg_blocking_pids(:blocked_backend_pid)
                        )
                        AND wait_event_type = 'Lock'
                        AND wait_event = 'advisory'
                    FROM pg_stat_activity
                    WHERE pid = :blocked_backend_pid
                    """
                ),
                {
                    "blocked_backend_pid": blocked_backend_pid,
                    "blocking_backend_pid": blocking_backend_pid,
                },
            )
            if waiting:
                return True
            time.sleep(0.02)
    return False


def test_speaker_slot_postgres_lock_uses_a_stable_per_slot_advisory_key() -> None:
    from app.services import provider_voice_registry, voice_slots

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
    assert voice_slots._speaker_slot_lock_id(" S_same ") == (
        provider_voice_registry.provider_voice_lock_key("S_same")
    )


def test_retired_speaker_slot_assignment_does_not_lock_or_scan(
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
        with pytest.raises(AppError) as exc:
            voice_slots.assign_speaker_slot(
                db,
                tenant_slug="acme",
                speaker_id="S_concurrency_guard",
                apply=True,
                platform_speaker_ids=[],
            )

    assert exc.value.code == "VOICE_SLOT_ASSIGNMENT_RETIRED"
    assert lock_calls == []


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the real advisory-lock test.",
)
def test_postgres_official_registration_serializes_against_customer_claim(
    monkeypatch,
) -> None:
    from app.db.models import (
        Asset,
        Base,
        BillingOperation,
        BrandVoice,
        BrandVoiceOrder,
        BrandVoiceProviderId,
        Tenant,
        User,
    )
    from app.services import provider_voice_registry

    engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_pre_ping=True)
    schema = f"provider_voice_registry_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_engine = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(
        scoped_engine,
        tables=[
            Tenant.__table__,
            User.__table__,
            Asset.__table__,
            BillingOperation.__table__,
            BrandVoice.__table__,
            BrandVoiceOrder.__table__,
            BrandVoiceProviderId.__table__,
        ],
    )
    Session = sessionmaker(bind=scoped_engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        ["voice-race"],
    )
    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_voice_clone_speaker_ids",
        [],
    )
    first = Session()
    second_started = threading.Event()
    second_done = threading.Event()
    second_backend_pids: list[int] = []
    second_errors: list[BaseException] = []
    thread: threading.Thread | None = None

    try:
        first_backend_pid = int(first.scalar(text("SELECT pg_backend_pid()")))
        provider_voice_registry.register_official_provider_voice_ids(
            first,
            provider_voice_ids=["voice-race"],
        )

        def claim() -> None:
            try:
                with Session() as second:
                    second_backend_pids.append(
                        int(second.scalar(text("SELECT pg_backend_pid()")))
                    )
                    second_started.set()
                    provider_voice_registry.claim_customer_provider_voice_id(
                        second,
                        provider_voice_id="voice-race",
                        brand_voice_id="brand-never-inserted",
                        order_id="order-never-inserted",
                    )
            except BaseException as exc:  # noqa: BLE001 - thread failures are evidence.
                second_errors.append(exc)
            finally:
                second_done.set()

        thread = threading.Thread(target=claim, daemon=True)
        thread.start()
        assert second_started.wait(timeout=5)
        assert _wait_for_postgres_advisory_blocker(
            engine,
            blocked_backend_pid=second_backend_pids[0],
            blocking_backend_pid=first_backend_pid,
        )
        assert not second_done.is_set()

        first.commit()
        thread.join(timeout=10)

        assert not thread.is_alive()
        assert len(second_errors) == 1
        assert isinstance(second_errors[0], AppError)
        assert second_errors[0].code == "PROVIDER_VOICE_ID_CONFLICT"
        with Session() as verify:
            rows = list(
                verify.scalars(
                    select(BrandVoiceProviderId).where(
                        BrandVoiceProviderId.normalized_provider_id == "voice-race"
                    )
                )
            )
        assert len(rows) == 1
        assert rows[0].kind == "official"
    finally:
        first.rollback()
        first.close()
        if thread is not None:
            thread.join(timeout=5)
        if thread is None or not thread.is_alive():
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the real advisory-lock test.",
)
def test_postgres_brand_voice_cold_start_serializes_env_slot_allocation(
    monkeypatch,
) -> None:
    from app.api.v1.routes import brand_voices
    from app.db.models import Base, ProviderConfig, Tenant

    engine = create_engine(
        os.environ["TEST_POSTGRES_URL"],
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c lock_timeout=10s -c statement_timeout=15s",
        },
    )
    schema = f"brand_voice_slot_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_engine = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(
        scoped_engine,
        tables=[Tenant.__table__, ProviderConfig.__table__],
    )
    Session = sessionmaker(bind=scoped_engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(
        brand_voices,
        "settings",
        SimpleNamespace(
            engine_doubao_voice_clone_speaker_ids=[
                "S_env_pool_001",
                "S_env_pool_002",
            ]
        ),
    )

    first = Session()
    second_started = threading.Event()
    second_done = threading.Event()
    second_slots: list[str] = []
    second_errors: list[BaseException] = []
    second_backend_pids: list[int] = []
    thread = None
    first_slot = ""
    first_backend_pid = 0
    second_was_blocked = False
    second_waited_on_advisory = False
    config_count = 0
    used_speaker_ids: dict[str, str] = {}
    try:
        first.add_all(
            [
                Tenant(id="slot-tenant-one", slug="slot-tenant-one", name="Tenant One"),
                Tenant(id="slot-tenant-two", slug="slot-tenant-two", name="Tenant Two"),
            ]
        )
        first.commit()

        first_backend_pid = int(first.scalar(text("SELECT pg_backend_pid()")))
        first_slot = brand_voices._allocate_voice_clone_speaker_id(
            first,
            tenant_id="slot-tenant-one",
            brand_voice_id="brand-voice-one",
        )
        assert first.in_transaction()

        def allocate_second() -> None:
            try:
                with Session() as second:
                    second_backend_pids.append(
                        int(second.scalar(text("SELECT pg_backend_pid()")))
                    )
                    second_started.set()
                    second_slots.append(
                        brand_voices._allocate_voice_clone_speaker_id(
                            second,
                            tenant_id="slot-tenant-two",
                            brand_voice_id="brand-voice-two",
                        )
                    )
                    second.commit()
            except BaseException as exc:  # noqa: BLE001 - thread failures are evidence.
                second_errors.append(exc)
            finally:
                second_done.set()

        thread = threading.Thread(target=allocate_second, daemon=True)
        thread.start()
        assert second_started.wait(timeout=5)
        second_waited_on_advisory = _wait_for_postgres_advisory_blocker(
            engine,
            blocked_backend_pid=second_backend_pids[0],
            blocking_backend_pid=first_backend_pid,
        )
        second_was_blocked = not second_done.is_set()

        first.commit()
        thread.join(timeout=10)

        assert not thread.is_alive()
        assert second_errors == []
        assert first_slot == "S_env_pool_001"
        assert second_slots == ["S_env_pool_002"]

        with Session() as verify:
            configs = list(
                verify.scalars(
                    select(ProviderConfig).where(
                        ProviderConfig.tenant_id.is_(None),
                        ProviderConfig.capability == "voice_clone",
                        ProviderConfig.provider == "doubao-voice-clone",
                    )
                )
            )
            config_count = len(configs)
            if configs:
                used_speaker_ids = dict(configs[0].config.get("used_speaker_ids") or {})

        assert config_count == 1
        assert used_speaker_ids == {
            "S_env_pool_001": "brand-voice-one",
            "S_env_pool_002": "brand-voice-two",
        }
        assert second_was_blocked
        assert second_waited_on_advisory
    finally:
        first.rollback()
        first.close()
        if thread is not None:
            thread.join(timeout=5)
            if thread.is_alive() and second_backend_pids:
                with engine.connect().execution_options(
                    isolation_level="AUTOCOMMIT"
                ) as connection:
                    connection.execute(
                        text("SELECT pg_cancel_backend(:backend_pid)"),
                        {"backend_pid": second_backend_pids[0]},
                    )
                thread.join(timeout=5)
        thread_is_alive = thread is not None and thread.is_alive()
        if not thread_is_alive:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
        if thread_is_alive:
            pytest.fail("PostgreSQL allocation thread did not stop after cancellation.")
