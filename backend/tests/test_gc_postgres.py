from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, GcCandidate, Tenant
from app.services.gc.observation import GcObjectObservation, observe_gc_candidates


@pytest.fixture(scope="module")
def postgres_gc_session_factory():
    postgres_url = os.getenv("TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("TEST_POSTGRES_URL is required for the real PostgreSQL GC test.")

    engine = create_engine(
        postgres_url,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c lock_timeout=10s -c statement_timeout=15s",
        },
    )
    schema = f"gc_observation_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_engine = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(
        scoped_engine,
        tables=[Tenant.__table__, GcCandidate.__table__],
    )
    factory = sessionmaker(bind=scoped_engine, autoflush=False, autocommit=False)
    try:
        yield factory
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_postgres_persists_jsonb_evidence_and_two_scan_eligibility(
    postgres_gc_session_factory,
) -> None:
    factory = postgres_gc_session_factory
    tenant_id = "gc-postgres-tenant"
    first_scan = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    observation = GcObjectObservation(
        tenant_id=tenant_id,
        key=f"tenants/{tenant_id}/uploads/orphan.bin",
        size=5,
        last_modified=first_scan - timedelta(days=2),
        version_id="version-one",
        etag="etag-one",
        referenced=False,
        reference_evidence={"checked_surfaces": ["surface-a"], "matched_surfaces": []},
    )

    with factory() as db:
        db.add(Tenant(id=tenant_id, slug=tenant_id, name="GC PostgreSQL Tenant"))
        db.commit()
        observe_gc_candidates(
            db,
            bucket="media",
            scan_id="postgres-first-scan",
            schema_fingerprint="f" * 64,
            scanned_at=first_scan,
            observations=[observation],
        )
        db.commit()

    with factory() as db:
        observe_gc_candidates(
            db,
            bucket="media",
            scan_id="postgres-second-scan",
            schema_fingerprint="f" * 64,
            scanned_at=first_scan + timedelta(days=7),
            observations=[observation],
        )
        db.commit()
        candidate = db.scalar(select(GcCandidate))

    assert candidate.status == "eligible"
    assert candidate.clean_scan_count == 2
    assert candidate.evidence["matched_surfaces"] == []
