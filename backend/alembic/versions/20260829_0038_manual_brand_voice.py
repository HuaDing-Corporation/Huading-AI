"""Add manual brand voice delivery, provider registry, and refund grants."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "20260829_0038"
down_revision = "20260829_0037"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(JSONB(), "postgresql")


_ORDER_STATE = """
(
  status = 'awaiting_fulfillment'
  AND fulfilled_brand_voice_id IS NULL AND fulfilled_provider_id IS NULL
  AND resolver_user_id IS NULL AND fulfilled_at IS NULL
  AND rejected_at IS NULL AND rejection_reason IS NULL
)
OR
(
  status = 'fulfilled'
  AND fulfilled_brand_voice_id IS NOT NULL AND fulfilled_provider_id IS NOT NULL
  AND resolver_user_id IS NOT NULL AND fulfilled_at IS NOT NULL
  AND rejected_at IS NULL AND rejection_reason IS NULL
)
OR
(
  status = 'rejected'
  AND fulfilled_brand_voice_id IS NULL AND fulfilled_provider_id IS NULL
  AND resolver_user_id IS NOT NULL AND fulfilled_at IS NULL
  AND rejected_at IS NOT NULL AND rejection_reason IS NOT NULL
  AND LENGTH(TRIM(rejection_reason)) > 0
)
"""
_ORDER_EXISTING_VOICE = (
    "(order_type = 'create' AND existing_brand_voice_id IS NULL) OR "
    "(order_type = 'renew' AND existing_brand_voice_id IS NOT NULL)"
)
_ORDER_RENEWAL_FULFILLMENT = (
    "order_type != 'renew' OR status != 'fulfilled' OR "
    "fulfilled_brand_voice_id = existing_brand_voice_id"
)
_REFUND_AMOUNT = "amount_credits > 0 AND amount_credits = CAST(amount_credits AS INTEGER)"
_REFUND_STATE = (
    "(status = 'pending' AND target_subscription_id IS NULL AND applied_at IS NULL) OR "
    "(status = 'applied' AND target_subscription_id IS NOT NULL AND applied_at IS NOT NULL)"
)
_AUDIT_ACTIONS = (
    "action IN ('credits_adjust', 'plan_change', 'status_change', 'voice_slot_assign', "
    "'task_retry', 'brand_voice_order_audio_access', 'brand_voice_order_fulfill', "
    "'brand_voice_order_reject')"
)
_LEGACY_AUDIT_ACTIONS = (
    "action IN ('credits_adjust', 'plan_change', 'status_change', "
    "'voice_slot_assign', 'task_retry')"
)


def upgrade() -> None:
    with op.batch_alter_table("brand_voices") as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_brand_voices_owner_user_id", "users", ["owner_user_id"], ["id"], ondelete="RESTRICT"
        )

    op.create_table(
        "brand_voice_provider_ids",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("normalized_provider_id", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("brand_voice_id", sa.String(length=36), nullable=True),
        sa.Column("first_order_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
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
        sa.ForeignKeyConstraint(["brand_voice_id"], ["brand_voices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "normalized_provider_id", name="uq_brand_voice_provider_ids_normalized_id"
        ),
        sa.CheckConstraint(
            "kind IN ('official', 'customer')", name="ck_brand_voice_provider_ids_kind"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'retired')", name="ck_brand_voice_provider_ids_status"
        ),
    )
    op.create_index(
        "ix_brand_voice_provider_ids_brand_voice_id", "brand_voice_provider_ids", ["brand_voice_id"]
    )

    op.create_table(
        "brand_voice_orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("order_type", sa.String(length=16), nullable=False),
        sa.Column("requested_name", sa.String(length=30), nullable=False),
        sa.Column("source_audio_asset_id", sa.String(length=36), nullable=False),
        sa.Column("source_metadata_snapshot", _json_type(), nullable=False),
        sa.Column("consent_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("existing_brand_voice_id", sa.String(length=36), nullable=True),
        sa.Column("billing_operation_id", sa.String(length=36), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="awaiting_fulfillment"
        ),
        sa.Column("fulfilled_brand_voice_id", sa.String(length=36), nullable=True),
        sa.Column("fulfilled_provider_id", sa.String(length=36), nullable=True),
        sa.Column("resolver_user_id", sa.String(length=36), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["source_audio_asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["existing_brand_voice_id"], ["brand_voices.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["billing_operation_id"], ["billing_operations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["fulfilled_brand_voice_id"], ["brand_voices.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["fulfilled_provider_id"], ["brand_voice_provider_ids.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["resolver_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "billing_operation_id", name="uq_brand_voice_orders_billing_operation_id"
        ),
        sa.CheckConstraint(
            "order_type IN ('create', 'renew')", name="ck_brand_voice_orders_order_type"
        ),
        sa.CheckConstraint(
            "source_audio_asset_id IS NOT NULL AND consent_confirmed_at IS NOT NULL",
            name="ck_brand_voice_orders_source_and_consent",
        ),
        sa.CheckConstraint(_ORDER_EXISTING_VOICE, name="ck_brand_voice_orders_existing_voice"),
        sa.CheckConstraint(
            "status IN ('awaiting_fulfillment', 'fulfilled', 'rejected')",
            name="ck_brand_voice_orders_status",
        ),
        sa.CheckConstraint(
            f"({_ORDER_STATE}) IS TRUE", name="ck_brand_voice_orders_resolution_state"
        ),
        sa.CheckConstraint(
            _ORDER_RENEWAL_FULFILLMENT,
            name="ck_brand_voice_orders_renewal_fulfills_existing_voice",
        ),
    )
    op.create_index(
        "uq_brand_voice_orders_awaiting_renewal_per_voice",
        "brand_voice_orders",
        ["existing_brand_voice_id"],
        unique=True,
        postgresql_where=sa.text("order_type = 'renew' AND status = 'awaiting_fulfillment'"),
        sqlite_where=sa.text("order_type = 'renew' AND status = 'awaiting_fulfillment'"),
    )
    op.create_index(
        "ix_brand_voice_orders_queue_created_at", "brand_voice_orders", ["status", "created_at"]
    )
    op.create_index(
        "ix_brand_voice_orders_tenant_user_created_at",
        "brand_voice_orders",
        ["tenant_id", "user_id", "created_at"],
    )
    with op.batch_alter_table("brand_voice_provider_ids") as batch_op:
        batch_op.create_foreign_key(
            "fk_brand_voice_provider_ids_first_order_id",
            "brand_voice_orders",
            ["first_order_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_table(
        "credit_refund_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("billing_operation_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("source_subscription_id", sa.String(length=36), nullable=False),
        sa.Column("target_subscription_id", sa.String(length=36), nullable=True),
        sa.Column("amount_credits", sa.Numeric(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["billing_operation_id"], ["billing_operations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_subscription_id"], ["subscriptions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_subscription_id"], ["subscriptions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "billing_operation_id", name="uq_credit_refund_grants_billing_operation_id"
        ),
        sa.CheckConstraint(_REFUND_AMOUNT, name="ck_credit_refund_grants_amount_positive_finite"),
        sa.CheckConstraint(_REFUND_STATE, name="ck_credit_refund_grants_state"),
    )
    op.create_index(
        "ix_credit_refund_grants_tenant_user_created_at",
        "credit_refund_grants",
        ["tenant_id", "user_id", "created_at"],
    )

    with op.batch_alter_table("admin_audit_logs") as batch_op:
        batch_op.drop_constraint("ck_admin_audit_logs_action", type_="check")
        batch_op.create_check_constraint("ck_admin_audit_logs_action", _AUDIT_ACTIONS)


def downgrade() -> None:
    connection = op.get_bind()
    counts = {
        "brand_voice_orders": int(
            connection.scalar(sa.text("SELECT COUNT(*) FROM brand_voice_orders")) or 0
        ),
        "brand_voice_provider_ids": int(
            connection.scalar(sa.text("SELECT COUNT(*) FROM brand_voice_provider_ids")) or 0
        ),
        "credit_refund_grants": int(
            connection.scalar(sa.text("SELECT COUNT(*) FROM credit_refund_grants")) or 0
        ),
        "manual audit logs": int(
            connection.scalar(
                sa.text(
                    "SELECT COUNT(*) FROM admin_audit_logs WHERE action IN "
                    "('brand_voice_order_audio_access', 'brand_voice_order_fulfill', "
                    "'brand_voice_order_reject')"
                )
            )
            or 0
        ),
    }
    nonempty = [f"{name}={count}" for name, count in counts.items() if count]
    if nonempty:
        raise RuntimeError(
            "Cannot downgrade 20260829_0038: " + ", ".join(nonempty) + ". History was not modified."
        )

    with op.batch_alter_table("admin_audit_logs") as batch_op:
        batch_op.drop_constraint("ck_admin_audit_logs_action", type_="check")
        batch_op.create_check_constraint(
            "ck_admin_audit_logs_action",
            _LEGACY_AUDIT_ACTIONS,
        )
    op.drop_index(
        "ix_credit_refund_grants_tenant_user_created_at", table_name="credit_refund_grants"
    )
    op.drop_table("credit_refund_grants")
    with op.batch_alter_table("brand_voice_provider_ids") as batch_op:
        batch_op.drop_constraint("fk_brand_voice_provider_ids_first_order_id", type_="foreignkey")
    op.drop_index("ix_brand_voice_orders_tenant_user_created_at", table_name="brand_voice_orders")
    op.drop_index("ix_brand_voice_orders_queue_created_at", table_name="brand_voice_orders")
    op.drop_index(
        "uq_brand_voice_orders_awaiting_renewal_per_voice", table_name="brand_voice_orders"
    )
    op.drop_table("brand_voice_orders")
    op.drop_index(
        "ix_brand_voice_provider_ids_brand_voice_id", table_name="brand_voice_provider_ids"
    )
    op.drop_table("brand_voice_provider_ids")
    with op.batch_alter_table("brand_voices") as batch_op:
        batch_op.drop_constraint("fk_brand_voices_owner_user_id", type_="foreignkey")
        batch_op.drop_column("expires_at")
        batch_op.drop_column("activated_at")
        batch_op.drop_column("owner_user_id")
