from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect

from app.db.models import (
    GcAuditLog,
    GcCandidate,
    GcCandidateStatus,
    GcReclamationJob,
    GcSkipReason,
)


def test_gc_control_models_define_phase_one_state_and_identity(auth_db) -> None:
    inspector = inspect(auth_db.kw["bind"])

    assert {
        GcCandidate.__tablename__,
        GcReclamationJob.__tablename__,
        GcAuditLog.__tablename__,
    } <= set(inspector.get_table_names())
    assert GcCandidate.__table__.info["gc_reference_class"] == "gc_control"
    assert GcReclamationJob.__table__.info["gc_reference_class"] == "gc_control"
    assert GcAuditLog.__table__.info["gc_reference_class"] == "gc_control"
    assert {status.value for status in GcCandidateStatus} == {
        "observed",
        "eligible",
        "approved",
        "delete_pending",
        "deleting",
        "succeeded",
        "retry_wait",
        "skipped",
        "dead_letter",
    }
    assert {reason.value for reason in GcSkipReason} == {
        "invalid_key",
        "catalog_excluded",
        "tenant_unknown",
        "object_changed",
        "object_identity_unavailable",
        "reference_found",
        "reference_coverage_changed",
        "reference_scan_failed",
        "external_lease_unknown",
        "write_barrier_unavailable",
        "approval_stale",
        "storage_unavailable",
    }


def test_gc_control_migration_extends_current_single_head() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260718_0028_gc_phase_one_control_tables.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "20260718_0028"' in migration
    assert 'down_revision: str | None = "20260716_0027"' in migration
    assert '"gc_candidates"' in migration
    assert '"gc_reclamation_jobs"' in migration
    assert '"gc_audit_log"' in migration
    assert "delete_object" not in migration
