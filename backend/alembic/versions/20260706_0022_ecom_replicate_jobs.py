"""Add e-commerce replicate jobs."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260706_0022"
down_revision = "20260706_0021"
branch_labels = None
depends_on = None


def _json_type() -> sa.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "ecom_replicate_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="planning"),
        sa.Column("output_mode", sa.String(length=16), nullable=False),
        sa.Column("requested_size", sa.String(length=20), nullable=False),
        sa.Column("requested_aspect", sa.String(length=16), nullable=False),
        sa.Column("detail_fallback_size", sa.String(length=20), nullable=True),
        sa.Column("reference_image_asset_ids", _json_type(), nullable=False),
        sa.Column("product_image_asset_ids", _json_type(), nullable=False),
        sa.Column("product_info", _json_type(), nullable=False),
        sa.Column("selling_points", _json_type(), nullable=False),
        sa.Column("reference_analysis_json", _json_type(), nullable=False),
        sa.Column("template_mapping_json", _json_type(), nullable=False),
        sa.Column("generation_plan_json", _json_type(), nullable=False),
        sa.Column("validation_json", _json_type(), nullable=True),
        sa.Column("output_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_credits", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("credit_rate", sa.Numeric(12, 4), nullable=False, server_default="15"),
        sa.Column("analysis_provider", sa.String(length=40), nullable=True),
        sa.Column("analysis_model", sa.String(length=80), nullable=True),
        sa.Column(
            "render_provider",
            sa.String(length=40),
            nullable=False,
            server_default="apimart",
        ),
        sa.Column("render_model", sa.String(length=80), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('planning', 'plan_ready', 'generating', 'completed', "
            "'partial_failed', 'failed', 'cancelled')",
            name="ck_ecom_replicate_jobs_status",
        ),
        sa.CheckConstraint(
            "output_mode IN ('main', 'detail')",
            name="ck_ecom_replicate_jobs_output_mode",
        ),
    )
    op.create_index(
        "ix_ecom_replicate_jobs_tenant_created_at",
        "ecom_replicate_jobs",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_ecom_replicate_jobs_tenant_status",
        "ecom_replicate_jobs",
        ["tenant_id", "status"],
    )

    op.create_table(
        "ecom_replicate_outputs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(length=36),
            sa.ForeignKey("ecom_replicate_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("theme", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="planned"),
        sa.Column(
            "reference_asset_id",
            sa.String(length=36),
            sa.ForeignKey("assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "product_asset_id",
            sa.String(length=36),
            sa.ForeignKey("assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("requested_size", sa.String(length=20), nullable=False),
        sa.Column("requested_aspect", sa.String(length=16), nullable=False),
        sa.Column("actual_width", sa.Integer(), nullable=True),
        sa.Column("actual_height", sa.Integer(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("reference_analysis_json", _json_type(), nullable=True),
        sa.Column("template_mapping_json", _json_type(), nullable=True),
        sa.Column("validation_json", _json_type(), nullable=True),
        sa.Column(
            "asset_id",
            sa.String(length=36),
            sa.ForeignKey("assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("storage_key", sa.String(length=500), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('planned', 'generating', 'succeeded', 'failed')",
            name="ck_ecom_replicate_outputs_status",
        ),
        sa.UniqueConstraint("job_id", "index", name="uq_ecom_replicate_outputs_job_index"),
    )
    op.create_index(
        "ix_ecom_replicate_outputs_job_id",
        "ecom_replicate_outputs",
        ["job_id"],
    )
    op.create_index(
        "ix_ecom_replicate_outputs_tenant_id",
        "ecom_replicate_outputs",
        ["tenant_id"],
    )
    op.create_index(
        "ix_ecom_replicate_outputs_tenant_created_at",
        "ecom_replicate_outputs",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ecom_replicate_outputs_tenant_created_at",
        table_name="ecom_replicate_outputs",
    )
    op.drop_index("ix_ecom_replicate_outputs_tenant_id", table_name="ecom_replicate_outputs")
    op.drop_index("ix_ecom_replicate_outputs_job_id", table_name="ecom_replicate_outputs")
    op.drop_table("ecom_replicate_outputs")
    op.drop_index("ix_ecom_replicate_jobs_tenant_status", table_name="ecom_replicate_jobs")
    op.drop_index(
        "ix_ecom_replicate_jobs_tenant_created_at",
        table_name="ecom_replicate_jobs",
    )
    op.drop_table("ecom_replicate_jobs")
