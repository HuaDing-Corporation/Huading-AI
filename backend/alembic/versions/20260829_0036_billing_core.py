"""Add the billing operation coordination ledger."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "20260829_0036"
down_revision = "20260806_0035"
branch_labels = None
depends_on = None


BILLING_STATE_CHECK = """
(
  status = 'in_progress'
  AND completion_kind IS NULL
  AND completed_at IS NULL
  AND settled_credits = 0
  AND released_credits = 0
)
OR
(
  status = 'completed'
  AND completion_kind IN ('succeeded', 'failed', 'rejected')
  AND completed_at IS NOT NULL
  AND settled_credits + released_credits = requested_credits
)
"""

BILLING_ZERO_CHECK = """
(operation = 'cosyvoice_brand_voice_create' AND requested_credits = 0)
OR
(operation <> 'cosyvoice_brand_voice_create' AND requested_credits > 0)
"""

BILLING_COMPLETION_CHECK = """
(completion_kind NOT IN ('failed', 'rejected') OR
 (settled_credits = 0 AND released_credits = requested_credits))
AND
(completion_kind <> 'succeeded' OR requested_credits = 0 OR settled_credits > 0)
"""


def _json_type():
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "billing_operations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("quote_hash", sa.String(length=64), nullable=False),
        sa.Column("pricing_snapshot", _json_type(), nullable=False),
        sa.Column("requested_credits", sa.Numeric(18, 6), nullable=False),
        sa.Column("settled_credits", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("released_credits", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="in_progress"),
        sa.Column("completion_kind", sa.String(length=32), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_payload", _json_type(), nullable=True),
        sa.Column("error_payload", _json_type(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "operation",
            "idempotency_key",
            name="uq_billing_operations_tenant_user_operation_idempotency_key",
        ),
        sa.CheckConstraint(
            f"({BILLING_STATE_CHECK}) IS TRUE", name="ck_billing_operations_state"
        ),
        sa.CheckConstraint(BILLING_ZERO_CHECK, name="ck_billing_operations_zero_price"),
        sa.CheckConstraint(BILLING_COMPLETION_CHECK, name="ck_billing_operations_completion"),
        sa.CheckConstraint(
            "requested_credits >= 0 AND settled_credits >= 0 AND released_credits >= 0",
            name="ck_billing_operations_amounts_nonnegative",
        ),
    )
    op.create_index(
        "ix_billing_operations_tenant_created_at",
        "billing_operations",
        ["tenant_id", "created_at"],
    )
    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.add_column(sa.Column("billing_operation_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("billing_item_index", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("billing_pricing_line_index", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("provider_usage", _json_type(), nullable=True))
        batch_op.create_foreign_key(
            "fk_usage_records_billing_operation_id",
            "billing_operations",
            ["billing_operation_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_check_constraint(
            "ck_usage_records_billing_item_index_nonnegative",
            "billing_item_index IS NULL OR billing_item_index >= 0",
        )
        batch_op.create_check_constraint(
            "ck_usage_records_billing_pricing_line_index_nonnegative",
            "billing_pricing_line_index IS NULL OR billing_pricing_line_index >= 0",
        )
        batch_op.create_unique_constraint(
            "uq_usage_records_billing_operation_item_index",
            ["billing_operation_id", "billing_item_index"],
        )


def downgrade() -> None:
    billing_operation_count = int(
        op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM billing_operations")) or 0
    )
    if billing_operation_count:
        raise RuntimeError(
            "Cannot downgrade 20260829_0036: "
            f"{billing_operation_count} billing operation rows exist. "
            "Billing history was not modified."
        )

    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.drop_constraint(
            "uq_usage_records_billing_operation_item_index", type_="unique"
        )
        batch_op.drop_constraint(
            "ck_usage_records_billing_pricing_line_index_nonnegative", type_="check"
        )
        batch_op.drop_constraint(
            "ck_usage_records_billing_item_index_nonnegative", type_="check"
        )
        batch_op.drop_constraint("fk_usage_records_billing_operation_id", type_="foreignkey")
        batch_op.drop_column("provider_usage")
        batch_op.drop_column("billing_pricing_line_index")
        batch_op.drop_column("billing_item_index")
        batch_op.drop_column("billing_operation_id")
    op.drop_index("ix_billing_operations_tenant_created_at", table_name="billing_operations")
    op.drop_table("billing_operations")
