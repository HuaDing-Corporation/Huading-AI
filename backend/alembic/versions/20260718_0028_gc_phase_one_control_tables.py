"""Add Phase 1 garbage-collection control tables."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260718_0028"
down_revision: str | None = "20260716_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "gc_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="observed"),
        sa.Column("bucket", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=1024), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_clean_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clean_scan_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scan_id", sa.String(length=36), nullable=False),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("evidence", _json_type(), nullable=False),
        sa.Column("object_version_id", sa.String(length=255), nullable=True),
        sa.Column("object_etag", sa.String(length=255), nullable=True),
        sa.Column("object_size", sa.BigInteger(), nullable=False),
        sa.Column("object_last_modified", sa.DateTime(timezone=True), nullable=False),
        sa.Column("skip_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('observed', 'eligible', 'approved', 'delete_pending', "
            "'deleting', 'succeeded', 'retry_wait', 'skipped', 'dead_letter')",
            name="ck_gc_candidates_status",
        ),
        sa.CheckConstraint(
            "skip_reason IS NULL OR skip_reason IN ('invalid_key', 'catalog_excluded', "
            "'tenant_unknown', 'object_changed', 'object_identity_unavailable', "
            "'reference_found', 'reference_coverage_changed', 'reference_scan_failed', "
            "'external_lease_unknown', 'write_barrier_unavailable', 'approval_stale', "
            "'storage_unavailable')",
            name="ck_gc_candidates_skip_reason",
        ),
        sa.CheckConstraint(
            "clean_scan_count >= 0",
            name="ck_gc_candidates_clean_scan_count",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bucket", "key", name="uq_gc_candidates_bucket_key"),
    )
    op.create_index(
        "ix_gc_candidates_tenant_id", "gc_candidates", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_gc_candidates_tenant_status",
        "gc_candidates",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index("ix_gc_candidates_scan_id", "gc_candidates", ["scan_id"], unique=False)

    op.create_table(
        "gc_reclamation_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            sa.String(length=36),
            sa.ForeignKey("gc_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="delete_pending"
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("bucket", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=1024), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("object_version_id", sa.String(length=255), nullable=False),
        sa.Column("object_etag", sa.String(length=255), nullable=True),
        sa.Column("object_size", sa.BigInteger(), nullable=False),
        sa.Column("actor", sa.String(length=160), nullable=True),
        sa.Column(
            "actor_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('delete_pending', 'deleting', 'succeeded', 'retry_wait', "
            "'skipped', 'dead_letter')",
            name="ck_gc_reclamation_jobs_status",
        ),
        sa.CheckConstraint(
            "attempt >= 0 AND attempt <= 3",
            name="ck_gc_reclamation_jobs_attempt",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", name="uq_gc_reclamation_jobs_candidate_id"),
    )
    op.create_index(
        "ix_gc_reclamation_jobs_tenant_id",
        "gc_reclamation_jobs",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_gc_reclamation_jobs_status_retry",
        "gc_reclamation_jobs",
        ["status", "next_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_gc_reclamation_jobs_tenant_status",
        "gc_reclamation_jobs",
        ["tenant_id", "status"],
        unique=False,
    )

    op.create_table(
        "gc_audit_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            sa.String(length=36),
            sa.ForeignKey("gc_candidates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "job_id",
            sa.String(length=36),
            sa.ForeignKey("gc_reclamation_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor", sa.String(length=160), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=1024), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("details", _json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_gc_audit_log_tenant_id", "gc_audit_log", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_gc_audit_log_candidate_created_at",
        "gc_audit_log",
        ["candidate_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_gc_audit_log_tenant_created_at",
        "gc_audit_log",
        ["tenant_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_gc_audit_log_tenant_created_at", table_name="gc_audit_log")
    op.drop_index("ix_gc_audit_log_candidate_created_at", table_name="gc_audit_log")
    op.drop_index("ix_gc_audit_log_tenant_id", table_name="gc_audit_log")
    op.drop_table("gc_audit_log")
    op.drop_index(
        "ix_gc_reclamation_jobs_tenant_status", table_name="gc_reclamation_jobs"
    )
    op.drop_index(
        "ix_gc_reclamation_jobs_status_retry", table_name="gc_reclamation_jobs"
    )
    op.drop_index("ix_gc_reclamation_jobs_tenant_id", table_name="gc_reclamation_jobs")
    op.drop_table("gc_reclamation_jobs")
    op.drop_index("ix_gc_candidates_scan_id", table_name="gc_candidates")
    op.drop_index("ix_gc_candidates_tenant_status", table_name="gc_candidates")
    op.drop_index("ix_gc_candidates_tenant_id", table_name="gc_candidates")
    op.drop_table("gc_candidates")
