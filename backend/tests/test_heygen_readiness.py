"""0039 must pass real pricing gates, not merely an Alembic version string."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from test_health import _health_database_session, _ReadyRedis, _seed_persisted_ready_state

from app.api.deps import get_db_session, get_redis_client
from app.api.v1.routes import health
from app.core.config import settings
from app.db.models import Base
from app.main import app
from scripts.ops import pricing_closure_readiness as readiness
from scripts.ops import pricing_closure_readiness_cli as cli


@pytest.fixture
def heygen_ready_db(db_session, monkeypatch, request):
    _seed_persisted_ready_state(db_session, monkeypatch)
    monkeypatch.setattr(settings, "environment", "production")
    # Independent release-contract DDL: do not derive expected constraints from
    # the gate itself. Replaces only the empty table in this isolated test DB.
    db_session.execute(text("DROP TABLE IF EXISTS avatar_provider_runs"))
    ddl = """
        CREATE TABLE avatar_provider_runs (
            task_id VARCHAR(36) NOT NULL PRIMARY KEY,
            tenant_id VARCHAR(36) NOT NULL,
            model VARCHAR(32) NOT NULL,
            state VARCHAR(16) NOT NULL DEFAULT 'ready',
            owner VARCHAR(36), lease_until TIMESTAMP,
            idempotency_key VARCHAR(255) NOT NULL,
            request_fingerprint VARCHAR(64), request_body JSON,
            submitted_at TIMESTAMP, input_expires_at TIMESTAMP,
            provider_job_id VARCHAR(128), checkpoint JSON NOT NULL DEFAULT '{}',
            next_check_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (idempotency_key),
            FOREIGN KEY(task_id) REFERENCES video_tasks (id) ON DELETE CASCADE,
            FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
            CONSTRAINT ck_avatar_provider_runs_model
                CHECK (model IN ('avatar_iv', 'lipsync_precision')),
            CONSTRAINT ck_avatar_provider_runs_state
                CHECK (state IN ('ready', 'active', 'pending', 'review', 'completed', 'failed'))
        )
    """
    mutation = getattr(request, "param", None)
    if mutation:
        old, new = mutation
        assert old in ddl
        ddl = ddl.replace(old, new, 1)
    db_session.execute(text(ddl))
    db_session.execute(
        text(
            "CREATE INDEX ix_avatar_provider_runs_due ON avatar_provider_runs(state, next_check_at)"
        )
    )
    db_session.execute(
        text("CREATE INDEX ix_avatar_provider_runs_tenant_id ON avatar_provider_runs(tenant_id)")
    )
    db_session.execute(text("UPDATE alembic_version SET version_num = '20260915_0039'"))
    db_session.commit()
    health._reset_pricing_readiness_cache()
    yield db_session
    health._reset_pricing_readiness_cache()
    db_session.rollback()
    db_session.execute(text("DROP TABLE IF EXISTS avatar_provider_runs"))
    db_session.commit()


@pytest.mark.parametrize("mode", ["audit", "preflight"])
def test_0039_real_gate_and_redacted_cli_accept_valid_schema(heygen_ready_db, mode):
    exit_code, payload = readiness._execute_cli_mode(heygen_ready_db, mode=mode)
    assert exit_code == 0, payload["blockers"]
    assert payload["migration_revision"] == "20260915_0039"
    validated_code, safe_payload = cli._validated_payload(mode, exit_code, payload)
    assert validated_code == 0
    assert safe_payload["ready"] is True


def test_0039_production_health_uses_compatible_gate(heygen_ready_db):
    app.dependency_overrides[get_db_session] = _health_database_session(heygen_ready_db)
    app.dependency_overrides[get_redis_client] = lambda: _ReadyRedis()
    try:
        response = TestClient(app).get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_redis_client, None)
    assert response.status_code == 200
    component = next(
        item for item in response.json()["data"]["components"] if item["name"] == "pricing_closure"
    )
    assert component["status"] == "ok"


def test_0038_pricing_audit_does_not_authorize_new_application(heygen_ready_db):
    heygen_ready_db.execute(text("UPDATE alembic_version SET version_num = '20260829_0038'"))
    heygen_ready_db.execute(text("DROP TABLE avatar_provider_runs"))
    heygen_ready_db.commit()
    assert readiness.pricing_closure_readiness(heygen_ready_db, production_mode=True).ready
    assert health._cached_pricing_closure_ready(heygen_ready_db) is False


@pytest.mark.parametrize(
    "statement, missing",
    [
        ("DROP TABLE avatar_provider_runs", "avatar_provider_runs"),
        (
            "ALTER TABLE avatar_provider_runs DROP COLUMN request_fingerprint",
            "avatar_provider_runs.request_fingerprint",
        ),
        (
            "DROP INDEX ix_avatar_provider_runs_due",
            "avatar_provider_runs.ix_avatar_provider_runs_due",
        ),
    ],
)
def test_0039_stamp_cannot_hide_missing_schema(heygen_ready_db, statement, missing):
    heygen_ready_db.execute(text(statement))
    heygen_ready_db.commit()
    report = readiness.pricing_closure_readiness(heygen_ready_db, production_mode=True)
    assert report.ready is False
    assert any(
        blocker.code == "PREFLIGHT_SCHEMA_MISSING" and missing in blocker.record_ids
        for blocker in report.blockers
    )


@pytest.mark.parametrize("revision", ["20260829_0038", "20990101_9999"])
def test_legacy_revision_retained_future_revision_rejected(heygen_ready_db, revision):
    heygen_ready_db.execute(
        text("UPDATE alembic_version SET version_num = :revision"), {"revision": revision}
    )
    heygen_ready_db.execute(text("DROP TABLE avatar_provider_runs"))
    heygen_ready_db.commit()
    report = readiness.pricing_closure_readiness(heygen_ready_db, production_mode=True)
    assert report.ready is (revision == "20260829_0038")
    if revision == "20990101_9999":
        assert any(blocker.code == "SCHEMA_NOT_READY" for blocker in report.blockers)


def test_0039_existing_official_registration_reaches_no_write_guard(heygen_ready_db):
    exit_code, payload = readiness._execute_cli_mode(heygen_ready_db, mode="register-official")
    assert exit_code == 2
    assert payload == {"error": "OFFICIAL_REGISTRATION_ALREADY_COMPLETE"}
    assert cli._validated_payload("register-official", exit_code, payload) == (exit_code, payload)


@pytest.mark.parametrize(
    "heygen_ready_db",
    [
        ("UNIQUE (idempotency_key),", ""),
        ("NOT NULL PRIMARY KEY", "NOT NULL"),
        ("idempotency_key VARCHAR(255) NOT NULL", "idempotency_key VARCHAR(255)"),
        ("checkpoint JSON NOT NULL", "checkpoint JSON"),
        (
            "REFERENCES video_tasks (id) ON DELETE CASCADE",
            "REFERENCES video_tasks (id) ON DELETE RESTRICT",
        ),
        ("CHECK (model IN ('avatar_iv', 'lipsync_precision'))", "CHECK (1=1)"),
        ("'avatar_iv'", "'Avatar_IV'"),
        ("'pending'", "'pen ding'"),
        (
            "CHECK (state IN ('ready', 'active', 'pending', 'review', 'completed', 'failed'))",
            "CHECK (1=1)",
        ),
    ],
    indirect=True,
)
def test_0039_missing_safety_constraint_blocks_readiness(heygen_ready_db):
    report = readiness.pricing_closure_readiness(heygen_ready_db, production_mode=True)
    assert report.ready is False
    assert any(
        blocker.code == "PREFLIGHT_SCHEMA_MISSING"
        and any(item.startswith("avatar_provider_runs.") for item in blocker.record_ids)
        for blocker in report.blockers
    )


def test_real_0039_migration_matches_postgres_readiness_contract():
    """CI checks real PostgreSQL constraint reflection, not a SQLite approximation."""
    url = os.environ.get("TEST_POSTGRES_URL") or os.environ.get("PRICING_READINESS_POSTGRES_URL")
    if not url:
        pytest.skip("An isolated PostgreSQL test database is required")
    engine = create_engine(url)
    schema = f"heygen_readiness_{uuid4().hex}"
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20260915_0039_avatar_provider_runs.py"
    )
    spec = importlib.util.spec_from_file_location("heygen_readiness_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            Base.metadata.create_all(connection)
            connection.execute(text("DROP TABLE IF EXISTS avatar_provider_runs"))
            module.op = Operations(MigrationContext.configure(connection))
            module.upgrade()
            with Session(bind=connection) as session:
                assert readiness._heygen_schema_blockers(session) == []
                connection.execute(
                    text(
                        "ALTER TABLE avatar_provider_runs DROP CONSTRAINT "
                        "avatar_provider_runs_idempotency_key_key"
                    )
                )
                blockers = readiness._heygen_schema_blockers(session)
                assert any(
                    "avatar_provider_runs.unique:idempotency_key" in item.record_ids
                    for item in blockers
                )
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
