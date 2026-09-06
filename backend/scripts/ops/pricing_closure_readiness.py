from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from io import StringIO
from pathlib import Path
from uuid import UUID

from alembic.runtime.migration import MigrationContext
from sqlalchemy import String, cast, inspect, select, text
from sqlalchemy.orm import Session

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import (
    BillingOperation,
    BrandVoiceOrder,
    CreditRate,
    CreditRefundGrant,
    Subscription,
    UsageRecord,
)
from app.db.session import SessionLocal
from app.services import billing_operations, brand_voice_orders, pricing, provider_voice_registry
from app.services.pricing import PricingInvariantError, validate_credit_rate_candidate

_REQUIRED_POLICY_DEFAULTS = {
    "script_generate": Decimal("1.0000"),
    "scene_prompt": Decimal("30.0000"),
    "ecom_cutout": Decimal("80.0000"),
    "ecom_model": Decimal("80.0000"),
    "video_create": Decimal("100.0000"),
}
_REQUIRED_RATE_FALLBACKS = {
    ("avatar", "second"): Decimal("180.0000"),
    ("video", "second"): Decimal("100.0000"),
    ("video_gen", "second"): Decimal("100.0000"),
    ("image", "image"): Decimal("80.0000"),
    ("reverse_prompt", "call"): Decimal("100.0000"),
}
_TARGET_MIGRATION_REVISION = "20260829_0038"
_SUPPORTED_PREFLIGHT_REVISIONS = {
    "20260724_0031",
    "20260804_0032",
    "20260805_0033",
    "20260805_0034",
    "20260806_0035",
    "20260829_0036",
    "20260829_0037",
    _TARGET_MIGRATION_REVISION,
}
_MIGRATION_0032_EXPECTED_RATES = {
    ("video", "second"): Decimal("80.0000"),
    ("video_gen", "second"): Decimal("80.0000"),
    ("image", "image"): Decimal("10.0000"),
}
_MIGRATION_0035_ALLOWED_RATES = {
    ("reverse_prompt", "call"): {
        Decimal("30.0000"),
        Decimal("100.0000"),
    },
    ("avatar", "second"): {
        Decimal("150.0000"),
        Decimal("180.0000"),
    },
}
_BEFORE_MIGRATION_0035 = {
    "20260724_0031",
    "20260804_0032",
    "20260805_0033",
    "20260805_0034",
}
_BEFORE_MIGRATION_0037 = {
    *_BEFORE_MIGRATION_0035,
    "20260806_0035",
    "20260829_0036",
}
_BEFORE_MIGRATION_0036 = {
    *_BEFORE_MIGRATION_0035,
    "20260806_0035",
}
_MIGRATION_0037_TARGETS = {
    "00000000-0000-0000-0000-000000000371": (
        "script_generate",
        "call",
        Decimal("1.0000"),
        {Decimal("1.0000")},
    ),
    "00000000-0000-0000-0000-000000000372": (
        "scene_prompt",
        "call",
        Decimal("30.0000"),
        {Decimal("30.0000")},
    ),
    "00000000-0000-0000-0000-000000000373": (
        "image",
        "image",
        Decimal("80.0000"),
        {Decimal("10.0000"), Decimal("80.0000")},
    ),
    "00000000-0000-0000-0000-000000000374": (
        "voice_clone",
        "call",
        Decimal("30000.0000"),
        {Decimal("30.0000"), Decimal("30000.0000")},
    ),
    "00000000-0000-0000-0000-000000000375": (
        "tts",
        "character",
        Decimal("0.1000"),
        {Decimal("0.1000")},
    ),
}
_APPROVED_PLATFORM_RATE_VALUES = {
    **_REQUIRED_RATE_FALLBACKS,
    **{
        (capability, unit): target
        for _, (capability, unit, target, _) in _MIGRATION_0037_TARGETS.items()
    },
}
_MIGRATION_0037_POSITIVE_RATE_PAIRS = {
    ("script_generate", "call"),
    ("scene_prompt", "call"),
    ("image", "image"),
    ("tts", "character"),
    ("avatar", "second"),
    ("video", "second"),
    ("video_gen", "second"),
    ("reverse_prompt", "call"),
}
_LEGACY_PREFLIGHT_SCHEMA = {
    "reasoning_wallets": {"tenant_id", "available_credits"},
    "tenants": {"id"},
    "users": {"id"},
    "chat_messages": {"id"},
    "assets": {"id"},
    "admin_audit_logs": {"id", "action"},
    "credit_rates": {
        "id",
        "tenant_id",
        "capability",
        "unit",
        "credits_per_unit",
        "is_active",
        "effective_at",
    },
    "usage_records": {
        "id",
        "subscription_id",
        "capability",
        "unit",
        "quantity",
        "credits",
        "status",
    },
    "subscriptions": {
        "id",
        "tenant_id",
        "status",
        "quota_credits_total",
        "quota_credits_used",
        "quota_credits_reserved",
    },
    "provider_configs": {"id", "config"},
    "brand_voices": {"id", "provider", "speaker_id"},
}
_MIGRATION_0036_PREFLIGHT_SCHEMA = {
    "billing_operations": {
        "id",
        "tenant_id",
        "user_id",
        "operation",
        "idempotency_key",
        "request_hash",
        "quote_hash",
        "pricing_snapshot",
        "requested_credits",
        "settled_credits",
        "released_credits",
        "status",
        "completion_kind",
        "completed_at",
        "result_type",
        "result_id",
        "result_payload",
        "error_code",
        "error_http_status",
        "error_payload",
        "created_at",
        "updated_at",
    },
    "usage_records": {
        "billing_operation_id",
        "billing_item_index",
        "billing_pricing_line_index",
        "provider_usage",
    },
}
_MIGRATION_0034_PREFLIGHT_SCHEMA = {
    "aibrain_user_cooldowns": {
        "id",
        "tenant_id",
        "user_id",
        "reason",
        "source_message_id",
        "expires_at",
        "created_at",
        "updated_at",
    }
}
_MIGRATION_0036_NAMED_CHECKS = {
    "billing_operations": {
        "ck_billing_operations_state",
        "ck_billing_operations_zero_price",
        "ck_billing_operations_completion",
        "ck_billing_operations_amounts_finite",
        "ck_billing_operations_amounts_nonnegative",
    },
    "usage_records": {
        "ck_usage_records_billing_item_index_nonnegative",
        "ck_usage_records_billing_pricing_line_index_nonnegative",
        "ck_usage_records_billing_allocation",
    },
}
_MIGRATION_0036_NAMED_UNIQUES = {
    "billing_operations": {
        "uq_billing_operations_tenant_user_operation_idempotency_key": (
            "tenant_id",
            "user_id",
            "operation",
            "idempotency_key",
        )
    },
    "usage_records": {
        "uq_usage_records_billing_operation_item_index": (
            "billing_operation_id",
            "billing_item_index",
        )
    },
}
_MIGRATION_0036_NAMED_INDEXES = {
    "billing_operations": {
        "ix_billing_operations_tenant_created_at": (("tenant_id", "created_at"), False)
    }
}
_MIGRATION_0037_NAMED_CHECKS = {
    "credit_rates": {"ck_credit_rates_credits_per_unit_valid"},
    "usage_records": {"ck_usage_records_amounts_valid"},
}
_MIGRATION_0037_NAMED_INDEXES = {
    "credit_rates": {
        "uq_credit_rates_platform_active_capability_unit": (("capability", "unit"), True),
        "uq_credit_rates_tenant_active_capability_unit": (
            ("tenant_id", "capability", "unit"),
            True,
        ),
    }
}
_PARTIAL_UNIQUE_INDEX_PREDICATES = {
    "uq_credit_rates_platform_active_capability_unit": ("tenant_id is null", "is_active"),
    "uq_credit_rates_tenant_active_capability_unit": ("tenant_id is not null", "is_active"),
    "uq_brand_voice_orders_awaiting_renewal_per_voice": (
        "order_type = 'renew'",
        "status = 'awaiting_fulfillment'",
    ),
}
_PARTIAL_PREDICATE_TOKEN = re.compile(
    r"""
    (?P<whitespace>\s+)
    |(?P<cast>::)
    |(?P<left_parenthesis>\()
    |(?P<right_parenthesis>\))
    |(?P<equals>=)
    |(?P<string>'(?:''|[^'])*')
    |(?P<quoted_identifier>"(?:""|[^"])*")
    |(?P<word>[A-Za-z_][A-Za-z0-9_$]*)
    """,
    re.VERBOSE,
)
_PARTIAL_PREDICATE_RESERVED_WORDS = frozenset({"AND", "FALSE", "IS", "NOT", "NULL", "OR", "TRUE"})


def _partial_predicate_tokens(value: str) -> tuple[tuple[str, str], ...] | None:
    """Tokenize only the small predicate grammar emitted by our indexes."""
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(value):
        match = _PARTIAL_PREDICATE_TOKEN.match(value, position)
        if match is None:
            return None
        position = match.end()
        kind = str(match.lastgroup)
        token_value = match.group()
        if kind == "whitespace":
            continue
        if kind == "string":
            token_value = token_value[1:-1].replace("''", "'")
        elif kind == "quoted_identifier":
            token_value = token_value[1:-1].replace('""', '"')
        tokens.append((kind, token_value))
    return tuple(tokens)


def _strip_partial_predicate_parentheses(
    tokens: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...] | None:
    while tokens and tokens[0][0] == "left_parenthesis":
        depth = 0
        closing_position: int | None = None
        for position, (kind, _value) in enumerate(tokens):
            if kind == "left_parenthesis":
                depth += 1
            elif kind == "right_parenthesis":
                depth -= 1
                if depth < 0:
                    return None
                if depth == 0:
                    closing_position = position
                    break
        if closing_position is None:
            return None
        if closing_position != len(tokens) - 1:
            break
        tokens = tokens[1:-1]
    return tokens


def _partial_predicate_conjuncts(
    tokens: tuple[tuple[str, str], ...],
) -> list[tuple[tuple[str, str], ...]] | None:
    tokens = _strip_partial_predicate_parentheses(tokens)
    if not tokens:
        return None
    depth = 0
    start = 0
    parts: list[tuple[tuple[str, str], ...]] = []
    for position, (kind, value) in enumerate(tokens):
        if kind == "left_parenthesis":
            depth += 1
        elif kind == "right_parenthesis":
            depth -= 1
            if depth < 0:
                return None
        elif depth == 0 and kind == "word" and value.upper() == "AND":
            nested = _partial_predicate_conjuncts(tokens[start:position])
            if nested is None:
                return None
            parts.extend(nested)
            start = position + 1
    if depth != 0:
        return None
    if not parts:
        return [tokens]
    nested = _partial_predicate_conjuncts(tokens[start:])
    if nested is None:
        return None
    parts.extend(nested)
    return parts


def _partial_predicate_operand(
    tokens: tuple[tuple[str, str], ...],
    *,
    kind: str,
    allow_text_cast: bool,
) -> str | None:
    tokens = _strip_partial_predicate_parentheses(tokens)
    if not tokens:
        return None
    while len(tokens) >= 3 and tokens[-2][0] == "cast" and tokens[-1][0] == "word":
        if not allow_text_cast or tokens[-1][1].lower() != "text":
            return None
        tokens = _strip_partial_predicate_parentheses(tokens[:-2])
        if not tokens:
            return None
    if len(tokens) != 1 or tokens[0][0] != kind:
        return None
    value = tokens[0][1]
    if kind == "word":
        if value.upper() in _PARTIAL_PREDICATE_RESERVED_WORDS:
            return None
        return value.lower()
    return value


def _canonical_partial_predicate_atom(
    tokens: tuple[tuple[str, str], ...],
) -> tuple[str, ...] | None:
    tokens = _strip_partial_predicate_parentheses(tokens)
    if not tokens:
        return None
    depth = 0
    operators: list[tuple[int, str]] = []
    for position, (kind, value) in enumerate(tokens):
        if kind == "left_parenthesis":
            depth += 1
        elif kind == "right_parenthesis":
            depth -= 1
            if depth < 0:
                return None
        elif depth == 0 and kind == "equals":
            operators.append((position, "equals"))
        elif depth == 0 and kind == "word" and value.upper() == "IS":
            operators.append((position, "is"))
    if depth != 0 or len(operators) > 1:
        return None
    if not operators:
        identifier = _partial_predicate_operand(tokens, kind="word", allow_text_cast=False)
        return None if identifier is None else ("truthy", identifier)

    position, operator = operators[0]
    identifier = _partial_predicate_operand(
        tokens[:position], kind="word", allow_text_cast=True
    )
    if identifier is None:
        return None
    if operator == "equals":
        literal = _partial_predicate_operand(
            tokens[position + 1 :], kind="string", allow_text_cast=True
        )
        return None if literal is None else ("equals", identifier, literal)

    suffix = tuple((kind, value.upper()) for kind, value in tokens[position + 1 :])
    if suffix == (("word", "NULL"),):
        return ("is_null", identifier)
    if suffix == (("word", "NOT"), ("word", "NULL")):
        return ("is_not_null", identifier)
    return None


def _canonical_partial_predicate(value: str) -> tuple[tuple[str, ...], ...] | None:
    tokens = _partial_predicate_tokens(value)
    if not tokens:
        return None
    conjuncts = _partial_predicate_conjuncts(tokens)
    if conjuncts is None:
        return None
    atoms = [_canonical_partial_predicate_atom(conjunct) for conjunct in conjuncts]
    if any(atom is None for atom in atoms):
        return None
    return tuple(sorted(atom for atom in atoms if atom is not None))


def _partial_unique_index_predicate_matches(
    index_name: str,
    where_clauses: list[object],
) -> bool:
    predicates = _PARTIAL_UNIQUE_INDEX_PREDICATES[index_name]
    if len(where_clauses) != 1:
        return False
    expected = _canonical_partial_predicate(" AND ".join(predicates))
    actual = _canonical_partial_predicate(str(where_clauses[0]))
    return expected is not None and actual == expected

_MIGRATION_0036_FOREIGN_KEYS = {
    "billing_operations": (
        (("tenant_id",), "tenants", ("id",), "RESTRICT"),
        (("user_id",), "users", ("id",), "RESTRICT"),
    ),
    "usage_records": (
        (("billing_operation_id",), "billing_operations", ("id",), "RESTRICT"),
    ),
}
_MIGRATION_0034_FOREIGN_KEYS = {
    "aibrain_user_cooldowns": (
        (("tenant_id",), "tenants", ("id",), "CASCADE"),
        (("user_id",), "users", ("id",), "CASCADE"),
        (("source_message_id",), "chat_messages", ("id",), "SET NULL"),
    )
}
_MIGRATION_0038_SCHEMA = {
    "brand_voices": {"owner_user_id", "activated_at", "expires_at"},
    "brand_voice_provider_ids": {
        "id",
        "provider",
        "normalized_provider_id",
        "kind",
        "brand_voice_id",
        "first_order_id",
        "status",
        "created_at",
        "updated_at",
    },
    "brand_voice_orders": {
        "id",
        "tenant_id",
        "user_id",
        "order_type",
        "requested_name",
        "source_audio_asset_id",
        "source_metadata_snapshot",
        "consent_confirmed_at",
        "existing_brand_voice_id",
        "billing_operation_id",
        "status",
        "fulfilled_brand_voice_id",
        "fulfilled_provider_id",
        "resolver_user_id",
        "fulfilled_at",
        "rejected_at",
        "rejection_reason",
        "created_at",
        "updated_at",
    },
    "credit_refund_grants": {
        "id",
        "billing_operation_id",
        "tenant_id",
        "user_id",
        "source_subscription_id",
        "target_subscription_id",
        "amount_credits",
        "status",
        "created_at",
        "applied_at",
    },
}
_MIGRATION_0038_NAMED_CHECKS = {
    "admin_audit_logs": {"ck_admin_audit_logs_action"},
    "brand_voice_provider_ids": {
        "ck_brand_voice_provider_ids_kind",
        "ck_brand_voice_provider_ids_status",
    },
    "brand_voice_orders": {
        "ck_brand_voice_orders_order_type",
        "ck_brand_voice_orders_source_and_consent",
        "ck_brand_voice_orders_existing_voice",
        "ck_brand_voice_orders_status",
        "ck_brand_voice_orders_resolution_state",
        "ck_brand_voice_orders_renewal_fulfills_existing_voice",
    },
    "credit_refund_grants": {
        "ck_credit_refund_grants_amount_positive_finite",
        "ck_credit_refund_grants_state",
    },
}
_MIGRATION_0038_NAMED_UNIQUES = {
    "brand_voice_provider_ids": {
        "uq_brand_voice_provider_ids_normalized_id": ("normalized_provider_id",)
    },
    "brand_voice_orders": {
        "uq_brand_voice_orders_billing_operation_id": ("billing_operation_id",)
    },
    "credit_refund_grants": {
        "uq_credit_refund_grants_billing_operation_id": ("billing_operation_id",)
    },
}
_MIGRATION_0038_NAMED_INDEXES = {
    "brand_voice_provider_ids": {
        "ix_brand_voice_provider_ids_brand_voice_id": (("brand_voice_id",), False)
    },
    "brand_voice_orders": {
        "uq_brand_voice_orders_awaiting_renewal_per_voice": (("existing_brand_voice_id",), True),
        "ix_brand_voice_orders_queue_created_at": (("status", "created_at"), False),
        "ix_brand_voice_orders_tenant_user_created_at": (
            ("tenant_id", "user_id", "created_at"),
            False,
        ),
    },
    "credit_refund_grants": {
        "ix_credit_refund_grants_tenant_user_created_at": (
            ("tenant_id", "user_id", "created_at"),
            False,
        )
    },
}
_MIGRATION_0038_FOREIGN_KEYS = {
    "brand_voices": ((("owner_user_id",), "users", ("id",), "RESTRICT"),),
    "brand_voice_provider_ids": (
        (("brand_voice_id",), "brand_voices", ("id",), "RESTRICT"),
        (("first_order_id",), "brand_voice_orders", ("id",), "RESTRICT"),
    ),
    "brand_voice_orders": (
        (("tenant_id",), "tenants", ("id",), "RESTRICT"),
        (("user_id",), "users", ("id",), "RESTRICT"),
        (("source_audio_asset_id",), "assets", ("id",), "RESTRICT"),
        (("existing_brand_voice_id",), "brand_voices", ("id",), "RESTRICT"),
        (("billing_operation_id",), "billing_operations", ("id",), "RESTRICT"),
        (("fulfilled_brand_voice_id",), "brand_voices", ("id",), "RESTRICT"),
        (("fulfilled_provider_id",), "brand_voice_provider_ids", ("id",), "RESTRICT"),
        (("resolver_user_id",), "users", ("id",), "RESTRICT"),
    ),
    "credit_refund_grants": (
        (("billing_operation_id",), "billing_operations", ("id",), "RESTRICT"),
        (("tenant_id",), "tenants", ("id",), "RESTRICT"),
        (("user_id",), "users", ("id",), "RESTRICT"),
        (("source_subscription_id",), "subscriptions", ("id",), "RESTRICT"),
        (("target_subscription_id",), "subscriptions", ("id",), "RESTRICT"),
    ),
}
_PROVIDER_ID_BLOCKER_CODES = {
    "UNKNOWN_HISTORIC_DOUBAO_ID",
    "OFFICIAL_DOUBAO_CONFIG_INVALID",
    "OFFICIAL_DOUBAO_REGISTRY_MISMATCH",
    "OFFICIAL_DOUBAO_CUSTOMER_REGISTRY_CONFLICT",
    "RETIRED_OFFICIAL_DOUBAO_ID",
}
_MAX_OFFICIAL_VOICE_IDS = 64


@dataclass(frozen=True)
class PricingReadinessBlocker:
    code: str
    record_ids: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class InventoryId:
    provider_voice_id: str


@dataclass(frozen=True)
class RateRow:
    id: str
    scope: str
    tenant_id: str | None
    capability: str
    unit: str
    credits_per_unit: str
    active: bool


@dataclass(frozen=True)
class LegacySlotWriteSurface:
    surface: str
    state: str


@dataclass(frozen=True)
class PricingReadinessReport:
    ready: bool
    production_mode: bool
    migration_revision: str | None
    generated_at: datetime
    platform_rates: tuple[RateRow, ...]
    tenant_rates: tuple[RateRow, ...]
    platform_rate_ids: tuple[str, ...]
    tenant_rate_ids: tuple[str, ...]
    inventory_unknown_ids: tuple[InventoryId, ...]
    provider_inventory: dict[str, tuple[str, ...]]
    legacy_slot_write_surfaces: tuple[LegacySlotWriteSurface, ...]
    blockers: tuple[PricingReadinessBlocker, ...]

    def as_dict(self) -> dict[str, object]:
        """Return a machine-readable report containing IDs, never credentials or URLs."""
        return {
            "ready": self.ready,
            "production_mode": self.production_mode,
            "migration_revision": self.migration_revision,
            "generated_at": self.generated_at.isoformat(),
            "platform_rates": [asdict(item) for item in self.platform_rates],
            "tenant_rates": [asdict(item) for item in self.tenant_rates],
            "platform_rate_ids": list(self.platform_rate_ids),
            "tenant_rate_ids": list(self.tenant_rate_ids),
            "inventory_unknown_ids": [asdict(item) for item in self.inventory_unknown_ids],
            "provider_inventory": {
                key: list(value) for key, value in self.provider_inventory.items()
            },
            "legacy_slot_write_surfaces": [
                asdict(item) for item in self.legacy_slot_write_surfaces
            ],
            "blockers": [
                {"code": item.code, "record_ids": list(item.record_ids), "detail": item.detail}
                for item in self.blockers
            ],
        }


@dataclass(frozen=True)
class PricingPreflightReport:
    ready: bool
    production_mode: bool
    migration_revision: str | None
    target_migration_revision: str
    generated_at: datetime
    blockers: tuple[PricingReadinessBlocker, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "production_mode": self.production_mode,
            "migration_revision": self.migration_revision,
            "target_migration_revision": self.target_migration_revision,
            "generated_at": self.generated_at.isoformat(),
            "blockers": [
                {"code": item.code, "record_ids": list(item.record_ids), "detail": item.detail}
                for item in self.blockers
            ],
        }


def _migration_revision(db: Session) -> str | None:
    return MigrationContext.configure(db.connection()).get_current_revision()


def _schema_not_ready_report(
    *,
    production_mode: bool,
    migration_revision: str | None,
    as_of: datetime,
) -> PricingReadinessReport:
    blocker_revision = migration_revision or "missing"
    return PricingReadinessReport(
        ready=False,
        production_mode=production_mode,
        migration_revision=migration_revision,
        generated_at=as_of,
        platform_rates=(),
        tenant_rates=(),
        platform_rate_ids=(),
        tenant_rate_ids=(),
        inventory_unknown_ids=(),
        provider_inventory={},
        legacy_slot_write_surfaces=(),
        blockers=(
            PricingReadinessBlocker(
                code="SCHEMA_NOT_READY",
                record_ids=(blocker_revision,),
                detail=(
                    "Full pricing audit requires database migration revision "
                    f"{_TARGET_MIGRATION_REVISION}."
                ),
            ),
        ),
    )


def _migration_0032_preflight_blockers(db: Session) -> list[PricingReadinessBlocker]:
    blockers: list[PricingReadinessBlocker] = []
    tenant_rate_ids = tuple(
        str(row.id)
        for row in db.execute(
            text("SELECT id FROM credit_rates WHERE tenant_id IS NOT NULL ORDER BY id")
        )
    )
    if tenant_rate_ids:
        blockers.append(
            PricingReadinessBlocker(
                code="MIGRATION_0032_TENANT_RATE_OVERRIDE",
                record_ids=tenant_rate_ids,
                detail=(
                    "Migration 0032 requires zero tenant credit-rate rows, including inactive rows."
                ),
            )
        )
    for (capability, unit), expected in _MIGRATION_0032_EXPECTED_RATES.items():
        rows = list(
            db.execute(
                text(
                    "SELECT id, credits_per_unit FROM credit_rates "
                    "WHERE tenant_id IS NULL AND capability = :capability "
                    "AND unit = :unit AND is_active IS TRUE ORDER BY id"
                ),
                {"capability": capability, "unit": unit},
            )
        )
        if len(rows) == 1 and Decimal(str(rows[0].credits_per_unit)) == expected:
            continue
        blockers.append(
            PricingReadinessBlocker(
                code="MIGRATION_0032_RATE_MISMATCH",
                record_ids=(tuple(str(row.id) for row in rows) or (f"{capability}/{unit}",)),
                detail=(
                    "Migration 0032 requires exactly one active platform rate "
                    "at its expected value."
                ),
            )
        )
    return blockers


def _schema_contract_blockers(
    db: Session,
    *,
    required_schema: dict[str, set[str]],
    named_checks: dict[str, set[str]],
    named_uniques: dict[str, dict[str, tuple[str, ...]]],
    named_indexes: dict[str, dict[str, tuple[tuple[str, ...], bool]]],
    foreign_keys: dict[str, tuple[tuple[tuple[str, ...], str, tuple[str, ...], str], ...]],
) -> list[PricingReadinessBlocker]:
    """Reflect required migration objects before any ORM audit can touch them."""
    database_inspector = inspect(db.connection())
    available_tables = set(database_inspector.get_table_names())
    blockers: list[PricingReadinessBlocker] = []
    for table_name, columns in required_schema.items():
        if table_name not in available_tables:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=(table_name,),
                    detail="A required migration table is missing.",
                )
            )
            continue
        present_columns = {
            str(column["name"]) for column in database_inspector.get_columns(table_name)
        }
        missing_columns = columns - present_columns
        if missing_columns:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=tuple(
                        f"{table_name}.{column_name}" for column_name in sorted(missing_columns)
                    ),
                    detail="Required migration columns are missing.",
                )
            )
    for table_name, names in named_checks.items():
        if table_name not in available_tables:
            continue
        present_names = {
            str(constraint["name"])
            for constraint in database_inspector.get_check_constraints(table_name)
            if constraint.get("name")
        }
        missing_names = names - present_names
        if missing_names:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=tuple(f"{table_name}.{name}" for name in sorted(missing_names)),
                    detail="Required named migration checks are missing.",
                )
            )
    for table_name, expected in named_uniques.items():
        if table_name not in available_tables:
            continue
        actual = {
            str(constraint["name"]): tuple(str(column) for column in constraint["column_names"])
            for constraint in database_inspector.get_unique_constraints(table_name)
            if constraint.get("name")
        }
        missing = [name for name, columns in expected.items() if actual.get(name) != columns]
        if missing:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=tuple(f"{table_name}.{name}" for name in sorted(missing)),
                    detail="Required named migration unique constraints are missing or invalid.",
                )
            )
    for table_name, expected in named_indexes.items():
        if table_name not in available_tables:
            continue
        actual = {
            str(index["name"]): (
                tuple(str(column) for column in index["column_names"]),
                bool(index["unique"]),
            )
            for index in database_inspector.get_indexes(table_name)
            if index.get("name")
        }
        missing = [name for name, shape in expected.items() if actual.get(name) != shape]
        for index in database_inspector.get_indexes(table_name):
            index_name = str(index.get("name"))
            predicates = _PARTIAL_UNIQUE_INDEX_PREDICATES.get(index_name)
            if not predicates or index_name in missing:
                continue
            where_clauses = [
                value
                for key, value in (index.get("dialect_options") or {}).items()
                if key.endswith("_where")
            ]
            if not _partial_unique_index_predicate_matches(index_name, where_clauses):
                missing.append(index_name)
        if missing:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=tuple(f"{table_name}.{name}" for name in sorted(set(missing))),
                    detail="Required named migration indexes are missing or invalid.",
                )
            )
    referenced_identifiers: set[tuple[str, tuple[str, ...]]] = set()
    for table_name, expected_foreign_keys in foreign_keys.items():
        if table_name not in available_tables:
            continue
        actual_foreign_keys = {
            (
                tuple(str(column) for column in foreign_key["constrained_columns"]),
                str(foreign_key["referred_table"]),
                tuple(str(column) for column in foreign_key["referred_columns"]),
                str((foreign_key.get("options") or {}).get("ondelete", "")).upper(),
            )
            for foreign_key in database_inspector.get_foreign_keys(table_name)
        }
        missing = [
            (columns, referred_table, referred_columns, ondelete)
            for columns, referred_table, referred_columns, ondelete in expected_foreign_keys
            if (columns, referred_table, referred_columns, ondelete) not in actual_foreign_keys
        ]
        if missing:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=tuple(
                        f"{table_name}.foreign_key:{','.join(columns)}->"
                        f"{referred_table}.{','.join(referred_columns)}"
                        for columns, referred_table, referred_columns, _ in missing
                    ),
                    detail="Required migration foreign keys are missing or invalid.",
                )
            )
        referenced_identifiers.update(
            (referred_table, referred_columns)
            for _, referred_table, referred_columns, _ in expected_foreign_keys
        )
    for table_name, columns in sorted(referenced_identifiers):
        if table_name not in available_tables:
            continue
        primary_key_columns = (
            database_inspector.get_pk_constraint(table_name).get("constrained_columns") or []
        )
        primary_key = tuple(str(column) for column in primary_key_columns)
        unique_columns = {
            tuple(str(column) for column in constraint["column_names"])
            for constraint in database_inspector.get_unique_constraints(table_name)
        }
        if primary_key != columns and columns not in unique_columns:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=(f"{table_name}.unique:{','.join(columns)}",),
                    detail="A migration foreign-key target is not uniquely identified.",
                )
            )
    return blockers


def _legacy_schema_preflight_blockers(
    db: Session,
    *,
    migration_revision: str | None,
) -> list[PricingReadinessBlocker]:
    database_inspector = inspect(db.connection())
    available_tables = set(database_inspector.get_table_names())
    blockers: list[PricingReadinessBlocker] = []
    required_schema = {
        table_name: set(columns) for table_name, columns in _LEGACY_PREFLIGHT_SCHEMA.items()
    }
    if migration_revision in {
        "20260805_0034",
        "20260806_0035",
        "20260829_0036",
        "20260829_0037",
    }:
        for table_name, columns in _MIGRATION_0034_PREFLIGHT_SCHEMA.items():
            required_schema.setdefault(table_name, set()).update(columns)
    if migration_revision in {"20260829_0036", "20260829_0037"}:
        for table_name, columns in _MIGRATION_0036_PREFLIGHT_SCHEMA.items():
            required_schema.setdefault(table_name, set()).update(columns)
    for table_name, required_columns in required_schema.items():
        if table_name not in available_tables:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=(table_name,),
                    detail="A required legacy preflight table is missing.",
                )
            )
            continue
        available_columns = {
            str(column["name"]) for column in database_inspector.get_columns(table_name)
        }
        missing_columns = required_columns - available_columns
        if missing_columns:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=tuple(
                        f"{table_name}.{column_name}" for column_name in sorted(missing_columns)
                    ),
                    detail="Required legacy preflight columns are missing.",
                )
            )
    required_checks: dict[str, set[str]] = {}
    if migration_revision in {"20260724_0031", "20260804_0032"}:
        required_checks["reasoning_wallets"] = {
            "ck_reasoning_wallets_available_nonnegative"
        }
    if migration_revision in _SUPPORTED_PREFLIGHT_REVISIONS - {
        _TARGET_MIGRATION_REVISION
    }:
        required_checks["admin_audit_logs"] = {"ck_admin_audit_logs_action"}
        required_checks["credit_rates"] = {"ck_credit_rates_capability"}
        required_checks["usage_records"] = {
            "ck_usage_records_capability",
            "ck_usage_records_unit",
        }
    for table_name, expected_names in required_checks.items():
        if table_name not in available_tables:
            continue
        available_names = {
            str(constraint["name"])
            for constraint in database_inspector.get_check_constraints(table_name)
            if constraint.get("name")
        }
        missing_names = expected_names - available_names
        if missing_names:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=tuple(
                        f"{table_name}.{constraint_name}"
                        for constraint_name in sorted(missing_names)
                    ),
                    detail="Required named legacy preflight checks are missing.",
                )
            )
    if migration_revision in _SUPPORTED_PREFLIGHT_REVISIONS - {
        "20260724_0031",
        "20260804_0032",
    } and "reasoning_wallets" in available_tables:
        retained_wallet_checks = {
            str(constraint["name"])
            for constraint in database_inspector.get_check_constraints("reasoning_wallets")
            if constraint.get("name")
        }
        if "ck_reasoning_wallets_available_nonnegative" in retained_wallet_checks:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=("reasoning_wallets.ck_reasoning_wallets_available_nonnegative",),
                    detail="Migration 0033's retired wallet check is still present.",
                )
            )

    named_checks: dict[str, set[str]] = {}
    named_uniques: dict[str, dict[str, tuple[str, ...]]] = {}
    named_indexes: dict[str, dict[str, tuple[tuple[str, ...], bool]]] = {}
    if migration_revision in {
        "20260805_0034",
        "20260806_0035",
        "20260829_0036",
        "20260829_0037",
    }:
        blockers.extend(
            _schema_contract_blockers(
                db,
                required_schema=_MIGRATION_0034_PREFLIGHT_SCHEMA,
                named_checks={},
                named_uniques={},
                named_indexes={},
                foreign_keys=_MIGRATION_0034_FOREIGN_KEYS,
            )
        )
    if migration_revision in {"20260829_0036", "20260829_0037"}:
        named_checks.update(_MIGRATION_0036_NAMED_CHECKS)
        named_uniques.update(_MIGRATION_0036_NAMED_UNIQUES)
        named_indexes.update(_MIGRATION_0036_NAMED_INDEXES)
    if migration_revision == "20260829_0037":
        for table_name, names in _MIGRATION_0037_NAMED_CHECKS.items():
            named_checks.setdefault(table_name, set()).update(names)
        named_indexes.update(_MIGRATION_0037_NAMED_INDEXES)
    for table_name, expected_names in named_checks.items():
        if table_name not in available_tables:
            continue
        actual_names = {
            str(constraint["name"])
            for constraint in database_inspector.get_check_constraints(table_name)
            if constraint.get("name")
        }
        missing_names = expected_names - actual_names
        if missing_names:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=tuple(
                        f"{table_name}.{name}" for name in sorted(missing_names)
                    ),
                    detail="Required named migration checks are missing.",
                )
            )
    for table_name, expected in named_uniques.items():
        if table_name not in available_tables:
            continue
        actual = {
            str(constraint["name"]): tuple(str(column) for column in constraint["column_names"])
            for constraint in database_inspector.get_unique_constraints(table_name)
            if constraint.get("name")
        }
        missing = [name for name, columns in expected.items() if actual.get(name) != columns]
        if missing:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=tuple(f"{table_name}.{name}" for name in sorted(missing)),
                    detail="Required named migration unique constraints are missing or invalid.",
                )
            )
    for table_name, expected in named_indexes.items():
        if table_name not in available_tables:
            continue
        actual = {
            str(index["name"]): (
                tuple(str(column) for column in index["column_names"]),
                bool(index["unique"]),
            )
            for index in database_inspector.get_indexes(table_name)
            if index.get("name")
        }
        missing = [name for name, shape in expected.items() if actual.get(name) != shape]
        if missing:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=tuple(f"{table_name}.{name}" for name in sorted(missing)),
                    detail="Required named migration indexes are missing or invalid.",
                )
            )
    if migration_revision in {
        "20260805_0034",
        "20260806_0035",
        "20260829_0036",
        "20260829_0037",
    } and "aibrain_user_cooldowns" in available_tables:
        table_name = "aibrain_user_cooldowns"
        foreign_keys = database_inspector.get_foreign_keys(table_name)
        expected_foreign_keys = (
            (("tenant_id",), "tenants", ("id",), "CASCADE"),
            (("user_id",), "users", ("id",), "CASCADE"),
            (("source_message_id",), "chat_messages", ("id",), "SET NULL"),
        )
        available_foreign_keys = {
            (
                tuple(str(column) for column in foreign_key["constrained_columns"]),
                str(foreign_key["referred_table"]),
                tuple(str(column) for column in foreign_key["referred_columns"]),
                str((foreign_key.get("options") or {}).get("ondelete", "")).upper(),
            )
            for foreign_key in foreign_keys
        }
        missing_foreign_keys = [
            (
                f"{table_name}.foreign_key:{','.join(columns)}->"
                f"{referred_table}.{','.join(referred_columns)}"
            )
            for columns, referred_table, referred_columns, ondelete in expected_foreign_keys
            if (columns, referred_table, referred_columns, ondelete)
            not in available_foreign_keys
        ]
        if missing_foreign_keys:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=tuple(sorted(missing_foreign_keys)),
                    detail="Required legacy preflight foreign keys are missing or invalid.",
                )
            )

        available_uniques = {
            str(constraint["name"]): tuple(
                str(column) for column in constraint["column_names"]
            )
            for constraint in database_inspector.get_unique_constraints(table_name)
            if constraint.get("name")
        }
        expected_uniques = {
            "uq_aibrain_user_cooldowns_user_id": ("user_id",),
        }
        missing_uniques = [
            f"{table_name}.{name}"
            for name, columns in expected_uniques.items()
            if available_uniques.get(name) != columns
        ]
        if missing_uniques:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=tuple(sorted(missing_uniques)),
                    detail="Required legacy preflight unique constraints are missing or invalid.",
                )
            )

        available_indexes = {
            str(index["name"]): (
                tuple(str(column) for column in index["column_names"]),
                bool(index["unique"]),
            )
            for index in database_inspector.get_indexes(table_name)
            if index.get("name")
        }
        expected_indexes = {
            "ix_aibrain_user_cooldowns_tenant_id": (("tenant_id",), False),
            "ix_aibrain_user_cooldowns_tenant_expires": (
                ("tenant_id", "expires_at"),
                False,
            ),
        }
        missing_indexes = [
            f"{table_name}.{name}"
            for name, shape in expected_indexes.items()
            if available_indexes.get(name) != shape
        ]
        if missing_indexes:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=tuple(sorted(missing_indexes)),
                    detail="Required legacy preflight indexes are missing or invalid.",
                )
            )
    if migration_revision in {"20260829_0036", "20260829_0037"}:
        required_schema = {
            table_name: set(columns)
            for table_name, columns in _MIGRATION_0036_PREFLIGHT_SCHEMA.items()
        }
        named_checks = {
            table_name: set(names) for table_name, names in _MIGRATION_0036_NAMED_CHECKS.items()
        }
        named_indexes = {
            table_name: dict(indexes)
            for table_name, indexes in _MIGRATION_0036_NAMED_INDEXES.items()
        }
        if migration_revision == "20260829_0037":
            for table_name, names in _MIGRATION_0037_NAMED_CHECKS.items():
                named_checks.setdefault(table_name, set()).update(names)
            named_indexes.update(_MIGRATION_0037_NAMED_INDEXES)
        blockers.extend(
            _schema_contract_blockers(
                db,
                required_schema=required_schema,
                named_checks=named_checks,
                named_uniques=_MIGRATION_0036_NAMED_UNIQUES,
                named_indexes=named_indexes,
                foreign_keys=_MIGRATION_0036_FOREIGN_KEYS,
            )
        )
    return blockers


def _target_schema_preflight_blockers(db: Session) -> list[PricingReadinessBlocker]:
    required_schema = {
        table_name: set(columns) for table_name, columns in _LEGACY_PREFLIGHT_SCHEMA.items()
    }
    for schema in (
        _MIGRATION_0034_PREFLIGHT_SCHEMA,
        _MIGRATION_0036_PREFLIGHT_SCHEMA,
        _MIGRATION_0038_SCHEMA,
    ):
        for table_name, columns in schema.items():
            required_schema.setdefault(table_name, set()).update(columns)
    named_checks = {
        "credit_rates": {"ck_credit_rates_capability", "ck_credit_rates_credits_per_unit_valid"},
        "usage_records": {
            "ck_usage_records_capability",
            "ck_usage_records_unit",
            "ck_usage_records_amounts_valid",
        },
    }
    for checks in (
        _MIGRATION_0036_NAMED_CHECKS,
        _MIGRATION_0037_NAMED_CHECKS,
        _MIGRATION_0038_NAMED_CHECKS,
    ):
        for table_name, names in checks.items():
            named_checks.setdefault(table_name, set()).update(names)
    named_indexes = {
        table_name: dict(indexes) for table_name, indexes in _MIGRATION_0036_NAMED_INDEXES.items()
    }
    named_indexes.update(_MIGRATION_0037_NAMED_INDEXES)
    named_indexes.update(_MIGRATION_0038_NAMED_INDEXES)
    foreign_keys = dict(_MIGRATION_0034_FOREIGN_KEYS)
    foreign_keys.update(_MIGRATION_0036_FOREIGN_KEYS)
    foreign_keys.update(_MIGRATION_0038_FOREIGN_KEYS)
    blockers = _schema_contract_blockers(
        db,
        required_schema=required_schema,
        named_checks=named_checks,
        named_uniques={
            **_MIGRATION_0036_NAMED_UNIQUES,
            **_MIGRATION_0038_NAMED_UNIQUES,
        },
        named_indexes=named_indexes,
        foreign_keys=foreign_keys,
    )
    database_inspector = inspect(db.connection())
    if "reasoning_wallets" in set(database_inspector.get_table_names()):
        retained_checks = {
            str(constraint["name"])
            for constraint in database_inspector.get_check_constraints("reasoning_wallets")
            if constraint.get("name")
        }
        if "ck_reasoning_wallets_available_nonnegative" in retained_checks:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_SCHEMA_MISSING",
                    record_ids=("reasoning_wallets.ck_reasoning_wallets_available_nonnegative",),
                    detail="Migration 0033's retired wallet check is still present.",
                )
            )
    return blockers


def _target_model_structure_is_missing(
    blockers: list[PricingReadinessBlocker],
) -> bool:
    required_schema = {
        table_name: set(columns) for table_name, columns in _LEGACY_PREFLIGHT_SCHEMA.items()
    }
    for schema in (
        _MIGRATION_0034_PREFLIGHT_SCHEMA,
        _MIGRATION_0036_PREFLIGHT_SCHEMA,
        _MIGRATION_0038_SCHEMA,
    ):
        for table_name, columns in schema.items():
            required_schema.setdefault(table_name, set()).update(columns)
    missing_relations = set(required_schema)
    missing_columns = {
        f"{table_name}.{column_name}"
        for table_name, columns in required_schema.items()
        for column_name in columns
    }
    return any(
        record_id in missing_relations or record_id in missing_columns
        for blocker in blockers
        for record_id in blocker.record_ids
    )


def _migration_0035_preflight_blockers(db: Session) -> list[PricingReadinessBlocker]:
    blockers: list[PricingReadinessBlocker] = []
    for (capability, unit), allowed_values in _MIGRATION_0035_ALLOWED_RATES.items():
        tenant_rows = list(
            db.execute(
                text(
                    "SELECT id FROM credit_rates WHERE tenant_id IS NOT NULL "
                    "AND capability = :capability AND unit = :unit "
                    "AND is_active IS TRUE ORDER BY id"
                ),
                {"capability": capability, "unit": unit},
            )
        )
        if tenant_rows:
            blockers.append(
                PricingReadinessBlocker(
                    code="MIGRATION_0035_TENANT_RATE_OVERRIDE",
                    record_ids=tuple(str(row.id) for row in tenant_rows),
                    detail=(
                        "Migration 0035 requires zero active tenant overrides for its target rate."
                    ),
                )
            )
        platform_rows = list(
            db.execute(
                text(
                    "SELECT id, credits_per_unit FROM credit_rates "
                    "WHERE tenant_id IS NULL AND capability = :capability "
                    "AND unit = :unit AND is_active IS TRUE ORDER BY id"
                ),
                {"capability": capability, "unit": unit},
            )
        )
        if (
            len(platform_rows) == 1
            and Decimal(str(platform_rows[0].credits_per_unit)) in allowed_values
        ):
            continue
        blockers.append(
            PricingReadinessBlocker(
                code="MIGRATION_0035_RATE_MISMATCH",
                record_ids=(
                    tuple(str(row.id) for row in platform_rows) or (f"{capability}/{unit}",)
                ),
                detail=(
                    "Migration 0035 requires exactly one active platform rate at an allowed value."
                ),
            )
        )
    return blockers


def _decimal_in_domain(
    raw_value: object,
    *,
    maximum: Decimal,
    quantum: Decimal,
) -> bool:
    try:
        value = Decimal(str(raw_value))
        return (
            value.is_finite()
            and value >= 0
            and value <= maximum
            and value == value.quantize(quantum)
        )
    except (ArithmeticError, ValueError):
        return False


def _migration_0037_preflight_blockers(
    db: Session,
    *,
    include_billing_operations: bool,
) -> list[PricingReadinessBlocker]:
    blockers: list[PricingReadinessBlocker] = []
    rate_rows = list(
        db.execute(
            text(
                "SELECT id, tenant_id, capability, unit, credits_per_unit, is_active "
                "FROM credit_rates ORDER BY id"
            )
        )
    )
    invalid_rate_ids = tuple(
        str(row.id)
        for row in rate_rows
        if not _decimal_in_domain(
            row.credits_per_unit,
            maximum=Decimal("99999999.9999"),
            quantum=Decimal("0.0001"),
        )
    )
    if invalid_rate_ids:
        blockers.append(
            PricingReadinessBlocker(
                code="MIGRATION_0037_INVALID_RATE_AMOUNT",
                record_ids=invalid_rate_ids,
                detail="Historic credit rates violate the migration 0037 numeric domain.",
            )
        )
    zero_rate_ids = tuple(
        str(row.id)
        for row in rate_rows
        if bool(row.is_active)
        and Decimal(str(row.credits_per_unit)) == 0
        and (
            (row.capability, row.unit) in _MIGRATION_0037_POSITIVE_RATE_PAIRS
            or (row.tenant_id is None and (row.capability, row.unit) == ("voice_clone", "call"))
        )
    )
    if zero_rate_ids:
        blockers.append(
            PricingReadinessBlocker(
                code="MIGRATION_0037_ACTIVE_ZERO_RATE",
                record_ids=zero_rate_ids,
                detail="Migration 0037 forbids zero for active billable rates.",
            )
        )

    active_groups: dict[tuple[str | None, str, str], list[str]] = defaultdict(list)
    for row in rate_rows:
        if not bool(row.is_active):
            continue
        active_groups[(row.tenant_id, row.capability, row.unit)].append(str(row.id))
    for ids in active_groups.values():
        if len(ids) > 1:
            blockers.append(
                PricingReadinessBlocker(
                    code="MIGRATION_0037_DUPLICATE_ACTIVE_RATE",
                    record_ids=tuple(ids),
                    detail="Migration 0037 requires unique active rates before creating indexes.",
                )
            )

    rows_by_id = {str(row.id): row for row in rate_rows}
    for seed_id, (capability, unit, target, allowed_values) in _MIGRATION_0037_TARGETS.items():
        active_platform_rows = [
            row
            for row in rate_rows
            if row.tenant_id is None
            and row.capability == capability
            and row.unit == unit
            and bool(row.is_active)
        ]
        drift_ids = tuple(
            str(row.id)
            for row in active_platform_rows
            if Decimal(str(row.credits_per_unit)) not in allowed_values
        )
        if drift_ids:
            blockers.append(
                PricingReadinessBlocker(
                    code="MIGRATION_0037_PLATFORM_RATE_DRIFT",
                    record_ids=drift_ids,
                    detail="Active platform rate is outside migration 0037 allowed CAS values.",
                )
            )
        seeded_row = rows_by_id.get(seed_id)
        if seeded_row is None:
            continue
        seeded_row_is_final = (
            seeded_row.tenant_id is None
            and seeded_row.capability == capability
            and seeded_row.unit == unit
            and bool(seeded_row.is_active)
            and Decimal(str(seeded_row.credits_per_unit)) == target
        )
        if not seeded_row_is_final:
            blockers.append(
                PricingReadinessBlocker(
                    code="MIGRATION_0037_SEED_ID_CONFLICT",
                    record_ids=(seed_id,),
                    detail="A reserved migration 0037 seed ID is occupied by a different row.",
                )
            )

    invalid_usage_ids = tuple(
        str(row.id)
        for row in db.execute(text("SELECT id, quantity, credits FROM usage_records ORDER BY id"))
        if not _decimal_in_domain(
            row.quantity,
            maximum=Decimal("999999999.999"),
            quantum=Decimal("0.001"),
        )
        or not _decimal_in_domain(
            row.credits,
            maximum=Decimal("999999999999.999999"),
            quantum=Decimal("0.000001"),
        )
    )
    if invalid_usage_ids:
        blockers.append(
            PricingReadinessBlocker(
                code="MIGRATION_0037_INVALID_USAGE_AMOUNT",
                record_ids=invalid_usage_ids,
                detail="Historic usage amounts violate the migration 0037 numeric domain.",
            )
        )
    if include_billing_operations:
        invalid_billing_ids = tuple(
            str(row.id)
            for row in db.execute(
                text(
                    "SELECT id, requested_credits, settled_credits, released_credits "
                    "FROM billing_operations ORDER BY id"
                )
            )
            if any(
                not _decimal_in_domain(
                    value,
                    maximum=Decimal("999999999999.999999"),
                    quantum=Decimal("0.000001"),
                )
                for value in (
                    row.requested_credits,
                    row.settled_credits,
                    row.released_credits,
                )
            )
        )
        if invalid_billing_ids:
            blockers.append(
                PricingReadinessBlocker(
                    code="MIGRATION_0037_INVALID_BILLING_AMOUNT",
                    record_ids=invalid_billing_ids,
                    detail="Historic billing amounts violate the migration 0037 numeric domain.",
                )
            )
    return blockers


def _legacy_config_ids(raw_ids: object) -> set[str]:
    if isinstance(raw_ids, str):
        candidates = raw_ids.split(",")
    elif isinstance(raw_ids, dict):
        candidates = raw_ids.keys()
    elif isinstance(raw_ids, (list, tuple, set)):
        candidates = raw_ids
    elif raw_ids is None:
        candidates = ()
    else:
        raise ValueError("unsupported provider ID collection")
    return {str(candidate).strip() for candidate in candidates if str(candidate).strip()}


def _legacy_inventory_preflight_blockers(
    db: Session,
    *,
    configured_official_ids: set[str],
) -> list[PricingReadinessBlocker]:
    blockers: list[PricingReadinessBlocker] = []
    sources_by_provider_id: dict[str, set[str]] = defaultdict(set)
    for provider_id in _legacy_config_ids(settings.engine_doubao_voice_clone_speaker_ids):
        sources_by_provider_id[provider_id].add("ENGINE_DOUBAO_VOICE_CLONE_SPEAKER_IDS")
    for row in db.execute(text("SELECT id, config FROM provider_configs ORDER BY id")):
        try:
            config = row.config
            if isinstance(config, str):
                config = json.loads(config)
            if config is None:
                config = {}
            if not isinstance(config, dict):
                raise ValueError("provider config must be an object")
            for key in ("speaker_ids", "used_speaker_ids"):
                for provider_id in _legacy_config_ids(config.get(key)):
                    sources_by_provider_id[provider_id].add(str(row.id))
        except (TypeError, ValueError, json.JSONDecodeError):
            blockers.append(
                PricingReadinessBlocker(
                    code="HISTORIC_DOUBAO_CONFIG_INVALID",
                    record_ids=(str(row.id),),
                    detail="Historic provider voice inventory has an invalid ID collection.",
                )
            )
    for row in db.execute(
        text(
            "SELECT id, speaker_id FROM brand_voices "
            "WHERE provider = :provider AND speaker_id IS NOT NULL ORDER BY id"
        ),
        {"provider": provider_voice_registry.DOUBAO_VOICE_CLONE_PROVIDER},
    ):
        raw_provider_id = str(row.speaker_id)
        normalized = raw_provider_id.strip()
        if not normalized:
            continue
        sources_by_provider_id[normalized].add(str(row.id))
        if normalized != raw_provider_id:
            blockers.append(
                PricingReadinessBlocker(
                    code="HISTORIC_DOUBAO_ID_NOT_NORMALIZED",
                    record_ids=(str(row.id),),
                    detail="Historic BrandVoice provider ID contains surrounding whitespace.",
                )
            )
    unknown_record_ids = tuple(
        sorted(
            {
                source_id
                for provider_id, source_ids in sources_by_provider_id.items()
                if provider_id not in configured_official_ids
                for source_id in source_ids
            }
        )
    )
    if unknown_record_ids:
        blockers.append(
            PricingReadinessBlocker(
                code="UNKNOWN_HISTORIC_DOUBAO_ID",
                record_ids=unknown_record_ids,
                detail="Historic provider IDs are not covered by the signed official list.",
            )
        )
    return blockers


def _legacy_wallet_preflight_blockers(db: Session) -> list[PricingReadinessBlocker]:
    blockers: list[PricingReadinessBlocker] = []
    reserved_rows = list(
        db.execute(
            text(
                "SELECT id, subscription_id, credits FROM usage_records "
                "WHERE status = 'reserved' ORDER BY id"
            )
        )
    )
    orphan_ids = tuple(str(row.id) for row in reserved_rows if row.subscription_id is None)
    if orphan_ids:
        blockers.append(
            PricingReadinessBlocker(
                code="PREFLIGHT_RESERVED_USAGE_WITHOUT_SUBSCRIPTION",
                record_ids=orphan_ids,
                detail="Reserved legacy usage must identify the subscription wallet it holds.",
            )
        )

    expected_reserved: dict[str, Decimal] = defaultdict(Decimal)
    usage_ids_by_subscription: dict[str, list[str]] = defaultdict(list)
    invalid_wallet_usage_ids: list[str] = []
    for row in reserved_rows:
        if row.subscription_id is None:
            continue
        subscription_id = str(row.subscription_id)
        try:
            credits = Decimal(str(row.credits))
        except (ArithmeticError, ValueError, TypeError):
            credits = Decimal("NaN")
        if not credits.is_finite() or credits < 0:
            invalid_wallet_usage_ids.append(str(row.id))
        else:
            expected_reserved[subscription_id] += credits.to_integral_value(rounding=ROUND_CEILING)
        usage_ids_by_subscription[subscription_id].append(str(row.id))
    if invalid_wallet_usage_ids:
        blockers.append(
            PricingReadinessBlocker(
                code="PREFLIGHT_WALLET_DOMAIN_FAILURE",
                record_ids=tuple(invalid_wallet_usage_ids),
                detail="Historic reserved usage contains an invalid wallet amount.",
            )
        )

    subscriptions = list(
        db.execute(
            text(
                "SELECT id, tenant_id, status, quota_credits_total, quota_credits_used, "
                "quota_credits_reserved FROM subscriptions ORDER BY id"
            )
        )
    )
    subscriptions_by_id = {str(row.id): row for row in subscriptions}
    missing_wallet_usage_ids = tuple(
        usage_id
        for subscription_id, usage_ids in usage_ids_by_subscription.items()
        if subscription_id not in subscriptions_by_id
        for usage_id in usage_ids
    )
    if missing_wallet_usage_ids:
        blockers.append(
            PricingReadinessBlocker(
                code="PREFLIGHT_RESERVED_USAGE_WALLET_MISSING",
                record_ids=missing_wallet_usage_ids,
                detail="Reserved legacy usage refers to a missing subscription wallet.",
            )
        )

    wallet_domain_ids: list[str] = []
    wallet_mismatch_ids: list[str] = []
    active_by_tenant: dict[str, list[str]] = defaultdict(list)
    for row in subscriptions:
        subscription_id = str(row.id)
        try:
            total = Decimal(str(row.quota_credits_total))
            used = Decimal(str(row.quota_credits_used))
            reserved = Decimal(str(row.quota_credits_reserved))
        except (ArithmeticError, ValueError, TypeError):
            total = used = reserved = Decimal("NaN")
        amounts_are_valid = all(
            value.is_finite() and value == value.to_integral_value()
            for value in (total, used, reserved)
        )
        if (
            not amounts_are_valid
            or total < 0
            or used < 0
            or reserved < 0
            or used + reserved > total
        ):
            wallet_domain_ids.append(subscription_id)
        if amounts_are_valid and reserved != expected_reserved.get(subscription_id, Decimal(0)):
            wallet_mismatch_ids.append(subscription_id)
        if str(row.status) == "active":
            active_by_tenant[str(row.tenant_id)].append(subscription_id)
    if wallet_domain_ids:
        blockers.append(
            PricingReadinessBlocker(
                code="PREFLIGHT_WALLET_DOMAIN_FAILURE",
                record_ids=tuple(wallet_domain_ids),
                detail="Historic wallet totals, used credits, and reservations are inconsistent.",
            )
        )
    if wallet_mismatch_ids:
        blockers.append(
            PricingReadinessBlocker(
                code="PREFLIGHT_WALLET_RESERVED_MISMATCH",
                record_ids=tuple(wallet_mismatch_ids),
                detail="Historic reserved wallet balance differs from reserved usage rows.",
            )
        )
    for ids in active_by_tenant.values():
        if len(ids) > 1:
            blockers.append(
                PricingReadinessBlocker(
                    code="PREFLIGHT_DUPLICATE_ACTIVE_SUBSCRIPTION",
                    record_ids=tuple(ids),
                    detail="A tenant has more than one active subscription wallet.",
                )
            )
    return blockers


def _production_environment_blockers(
    *,
    production_mode: bool,
) -> list[PricingReadinessBlocker]:
    if production_mode and str(settings.environment).strip().lower() not in {
        "prod",
        "production",
    }:
        return [
            PricingReadinessBlocker(
                code="PRODUCTION_ENVIRONMENT_REQUIRED",
                record_ids=(),
                detail="Production fail-closed checks require ENVIRONMENT=production.",
            )
        ]
    return []


def _official_config_validation_blockers(
    *,
    production_mode: bool,
) -> list[PricingReadinessBlocker]:
    if not production_mode:
        return []
    raw_ids = settings.engine_doubao_official_voice_ids
    invalid = not isinstance(raw_ids, (list, tuple)) or len(raw_ids) > _MAX_OFFICIAL_VOICE_IDS
    seen: set[str] = set()
    if not invalid:
        try:
            for raw_id in raw_ids:
                normalized = provider_voice_registry.normalize_provider_voice_id(str(raw_id))
                if normalized in seen:
                    invalid = True
                    break
                seen.add(normalized)
        except (AppError, TypeError, ValueError):
            invalid = True
    if not invalid:
        return []
    return [
        PricingReadinessBlocker(
            code="OFFICIAL_DOUBAO_CONFIG_INVALID",
            record_ids=(),
            detail="Configured official Doubao voice inventory is invalid.",
        )
    ]


def pricing_closure_preflight(
    db: Session,
    *,
    production_mode: bool,
) -> PricingPreflightReport:
    """Check whether a known release-line schema may enter the migration gate."""
    migration_revision = _migration_revision(db)
    if migration_revision == _TARGET_MIGRATION_REVISION:
        audit = pricing_closure_readiness(db, production_mode=production_mode)
        return PricingPreflightReport(
            ready=audit.ready,
            production_mode=production_mode,
            migration_revision=migration_revision,
            target_migration_revision=_TARGET_MIGRATION_REVISION,
            generated_at=audit.generated_at,
            blockers=audit.blockers,
        )
    blockers = _production_environment_blockers(production_mode=production_mode)
    if migration_revision not in _SUPPORTED_PREFLIGHT_REVISIONS:
        blockers.append(
            PricingReadinessBlocker(
                code="UNSUPPORTED_SCHEMA_REVISION",
                record_ids=((migration_revision or "missing"),),
                detail="Database revision is not on the approved 0031-to-0038 release line.",
            )
        )
    configured_ids = {
        str(value).strip()
        for value in settings.engine_doubao_official_voice_ids
        if str(value).strip()
    }
    if production_mode and not configured_ids:
        blockers.append(
            PricingReadinessBlocker(
                code="OFFICIAL_DOUBAO_CONFIG_MISSING",
                record_ids=(),
                detail="No exact signed official Doubao voice list is configured.",
            )
        )
    blockers.extend(_official_config_validation_blockers(production_mode=production_mode))
    legacy_schema_revision = migration_revision in _SUPPORTED_PREFLIGHT_REVISIONS - {
        _TARGET_MIGRATION_REVISION
    }
    legacy_schema_ready = True
    if legacy_schema_revision:
        schema_blockers = _legacy_schema_preflight_blockers(
            db,
            migration_revision=migration_revision,
        )
        blockers.extend(schema_blockers)
        legacy_schema_ready = not schema_blockers
    if legacy_schema_ready and migration_revision == "20260724_0031":
        blockers.extend(_migration_0032_preflight_blockers(db))
    if legacy_schema_ready and migration_revision in _BEFORE_MIGRATION_0035:
        blockers.extend(_migration_0035_preflight_blockers(db))
    has_billing_amount_columns = False
    if migration_revision == "20260829_0036":
        database_inspector = inspect(db.connection())
        if "billing_operations" in set(database_inspector.get_table_names()):
            has_billing_amount_columns = {
                str(column["name"])
                for column in database_inspector.get_columns("billing_operations")
            } >= {"id", "requested_credits", "settled_credits", "released_credits"}
    if legacy_schema_ready and migration_revision in _BEFORE_MIGRATION_0037 - {"20260829_0036"}:
        blockers.extend(
            _migration_0037_preflight_blockers(
                db,
                include_billing_operations=False,
            )
        )
    if migration_revision == "20260829_0036" and has_billing_amount_columns:
        blockers.extend(_migration_0037_preflight_blockers(db, include_billing_operations=True))
    if legacy_schema_ready and legacy_schema_revision:
        blockers.extend(
            _legacy_inventory_preflight_blockers(
                db,
                configured_official_ids=configured_ids,
            )
        )
    if legacy_schema_ready and migration_revision in _BEFORE_MIGRATION_0036:
        blockers.extend(_legacy_wallet_preflight_blockers(db))
    blockers.extend(_policy_default_blockers())
    surfaces = _legacy_slot_write_surfaces()
    blockers.extend(
        PricingReadinessBlocker(
            code="LEGACY_SLOT_WRITE_SURFACE",
            record_ids=(surface.surface,),
            detail="Legacy Doubao slot writing must be removed or explicitly retired.",
        )
        for surface in surfaces
        if surface.state != "retired"
    )
    return PricingPreflightReport(
        ready=not blockers,
        production_mode=production_mode,
        migration_revision=migration_revision,
        target_migration_revision=_TARGET_MIGRATION_REVISION,
        generated_at=datetime.now(UTC),
        blockers=tuple(blockers),
    )


def pricing_empty_bootstrap_preflight(
    db: Session,
    *,
    production_mode: bool,
) -> PricingPreflightReport:
    """Authorize migration only when the target database has no existing tables."""
    schema = "public" if db.get_bind().dialect.name == "postgresql" else None
    table_names = tuple(inspect(db.get_bind()).get_table_names(schema=schema))
    blockers = _production_environment_blockers(production_mode=production_mode)
    if table_names:
        blockers.append(
            PricingReadinessBlocker(
                code="EMPTY_BOOTSTRAP_DATABASE_NOT_EMPTY",
                record_ids=(),
                detail="Empty bootstrap requires a database with no existing tables.",
            )
        )
    configured_ids = {
        str(value).strip()
        for value in settings.engine_doubao_official_voice_ids
        if str(value).strip()
    }
    if production_mode and not configured_ids:
        blockers.append(
            PricingReadinessBlocker(
                code="OFFICIAL_DOUBAO_CONFIG_MISSING",
                record_ids=(),
                detail="No exact signed official Doubao voice list is configured.",
            )
        )
    blockers.extend(_official_config_validation_blockers(production_mode=production_mode))
    blockers.extend(_policy_default_blockers())
    blockers.extend(
        PricingReadinessBlocker(
            code="LEGACY_SLOT_WRITE_SURFACE",
            record_ids=(surface.surface,),
            detail="Legacy Doubao slot writing must be removed or explicitly retired.",
        )
        for surface in _legacy_slot_write_surfaces()
        if surface.state != "retired"
    )
    return PricingPreflightReport(
        ready=not blockers,
        production_mode=production_mode,
        migration_revision=None,
        target_migration_revision=_TARGET_MIGRATION_REVISION,
        generated_at=datetime.now(UTC),
        blockers=tuple(blockers),
    )


def _rate_blockers(
    db: Session,
) -> tuple[
    list[PricingReadinessBlocker],
    tuple[RateRow, ...],
    tuple[RateRow, ...],
]:
    # Read the numeric value as text: historic corruption such as SQLite's
    # textual NaN must become a blocker rather than crashing the audit's ORM
    # numeric result processor before validation can run.
    rows = list(
        db.execute(
            select(
                CreditRate.id,
                CreditRate.tenant_id,
                CreditRate.capability,
                CreditRate.unit,
                cast(CreditRate.credits_per_unit, String).label("credits_per_unit"),
                CreditRate.is_active,
            ).order_by(CreditRate.id)
        )
    )
    blockers: list[PricingReadinessBlocker] = []
    safe_values: dict[str, str] = {}
    validated_values: dict[str, Decimal] = {}
    for row in rows:
        try:
            value = Decimal(str(row.credits_per_unit))
            if not value.is_finite() or value <= 0:
                raise PricingInvariantError("stored credit rate must be finite and positive")
            validate_credit_rate_candidate(
                tenant_id=row.tenant_id,
                capability=row.capability,
                unit=row.unit,
                credits_per_unit=value,
                is_active=bool(row.is_active),
            )
            safe_values[row.id] = format(value.quantize(Decimal("0.0001")), "f")
            validated_values[row.id] = value
        except (PricingInvariantError, ArithmeticError, ValueError):
            safe_values[row.id] = "invalid"
            blockers.append(
                PricingReadinessBlocker(
                    code="INVALID_CREDIT_RATE",
                    record_ids=(row.id,),
                    detail="Stored credit rate is invalid.",
                )
            )
    active_groups: dict[tuple[str | None, str, str], list[str]] = defaultdict(list)
    for row in rows:
        if row.is_active:
            active_groups[(row.tenant_id, row.capability, row.unit)].append(row.id)
    for ids in active_groups.values():
        if len(ids) > 1:
            blockers.append(
                PricingReadinessBlocker(
                    code="DUPLICATE_ACTIVE_CREDIT_RATE",
                    record_ids=tuple(sorted(ids)),
                    detail="More than one active rate has the same scope, capability, and unit.",
                )
            )
    for row in rows:
        if not row.is_active or row.tenant_id is not None or row.id not in validated_values:
            continue
        expected = _APPROVED_PLATFORM_RATE_VALUES.get((row.capability, row.unit))
        if expected is None or validated_values[row.id] == expected:
            continue
        blockers.append(
            PricingReadinessBlocker(
                code="APPROVED_PLATFORM_RATE_MISMATCH",
                record_ids=(row.id,),
                detail="Active platform rate differs from the approved pricing contract.",
            )
        )
    safe_rows = tuple(
        RateRow(
            id=row.id,
            scope="platform" if row.tenant_id is None else "tenant",
            tenant_id=row.tenant_id,
            capability=row.capability,
            unit=row.unit,
            credits_per_unit=safe_values[row.id],
            active=bool(row.is_active),
        )
        for row in rows
    )
    return (
        blockers,
        tuple(row for row in safe_rows if row.scope == "platform"),
        tuple(row for row in safe_rows if row.scope == "tenant"),
    )


def _policy_default_blockers() -> list[PricingReadinessBlocker]:
    blockers: list[PricingReadinessBlocker] = []
    for operation, expected in _REQUIRED_POLICY_DEFAULTS.items():
        policy = pricing.PRICING_POLICIES.get(operation)
        if policy is None or Decimal(policy.default_unit_credits) != expected:
            blockers.append(
                PricingReadinessBlocker(
                    code="PRICING_POLICY_DEFAULT_MISMATCH",
                    record_ids=(operation,),
                    detail="Configured policy default differs from the pricing-closure contract.",
                )
            )
    for (capability, unit), expected in _REQUIRED_RATE_FALLBACKS.items():
        actual = pricing.DEFAULT_RATE_CREDITS.get((capability, unit))
        if actual is None or Decimal(actual) != expected:
            blockers.append(
                PricingReadinessBlocker(
                    code="DEFAULT_RATE_FALLBACK_MISMATCH",
                    record_ids=(f"{capability}/{unit}",),
                    detail="Configured fallback differs from the pricing-closure contract.",
                )
            )
    return blockers


def _billing_blockers(db: Session, *, as_of: datetime) -> list[PricingReadinessBlocker]:
    blockers: list[PricingReadinessBlocker] = []
    settled_lower_bounds: dict[str, int] = defaultdict(int)
    operations = list(db.scalars(select(BillingOperation).order_by(BillingOperation.id)))
    for operation in operations:
        try:
            lookup = billing_operations.lookup_operation(
                db,
                tenant_id=operation.tenant_id,
                user_id=operation.user_id,
                operation=operation.operation,
                idempotency_key=UUID(operation.idempotency_key),
            )
            if lookup is None:
                raise billing_operations.BillingInvariantError(
                    "billing operation lookup identity is invalid"
                )
            source_subscription_ids = set(
                db.scalars(
                    select(UsageRecord.subscription_id).where(
                        UsageRecord.billing_operation_id == operation.id
                    )
                )
            )
            if lookup.billing.settled_credits and (
                None in source_subscription_ids or len(source_subscription_ids) != 1
            ):
                raise billing_operations.BillingInvariantError(
                    "settled billing source subscription is invalid"
                )
            if lookup.billing.settled_credits:
                source_subscription_id = next(iter(source_subscription_ids))
                settled_lower_bounds[source_subscription_id] += lookup.billing.settled_credits
        except (
            billing_operations.BillingInvariantError,
            ArithmeticError,
            ValueError,
            TypeError,
        ):
            blockers.append(
                PricingReadinessBlocker(
                    code="BILLING_OPERATION_INVARIANT_FAILURE",
                    record_ids=(operation.id,),
                    detail="Billing operation invariant validation failed.",
                )
            )
        except AppError:
            blockers.append(
                PricingReadinessBlocker(
                    code="BILLING_OPERATION_INVARIANT_FAILURE",
                    record_ids=(operation.id,),
                    detail="Billing operation resource lookup failed.",
                )
            )
    for order in db.scalars(select(BrandVoiceOrder).order_by(BrandVoiceOrder.id)):
        try:
            brand_voice_orders.brand_voice_order_read(db, order=order)
        except (AppError, ArithmeticError, ValueError, TypeError):
            blockers.append(
                PricingReadinessBlocker(
                    code="BILLING_OPERATION_INVARIANT_FAILURE",
                    record_ids=(order.billing_operation_id,),
                    detail="Brand voice order billing link is invalid.",
                )
            )
    for grant in db.scalars(select(CreditRefundGrant).order_by(CreditRefundGrant.id)):
        order = db.scalar(
            select(BrandVoiceOrder).where(
                BrandVoiceOrder.billing_operation_id == grant.billing_operation_id
            )
        )
        try:
            if order is None:
                raise billing_operations.BillingInvariantError("refund grant order is missing")
            brand_voice_orders.brand_voice_order_read(db, order=order)
        except (AppError, ArithmeticError, ValueError, TypeError):
            blockers.append(
                PricingReadinessBlocker(
                    code="BILLING_REFUND_GRANT_INVARIANT_FAILURE",
                    record_ids=(grant.id,),
                    detail="Credit refund grant billing link is invalid.",
                )
            )
        if grant.status == "pending" and db.scalar(
            select(Subscription.id)
            .where(
                Subscription.tenant_id == grant.tenant_id,
                Subscription.status == "active",
                Subscription.period_start <= as_of,
                Subscription.period_end >= as_of,
            )
            .order_by(Subscription.id)
            .limit(1)
        ):
            blockers.append(
                PricingReadinessBlocker(
                    code="BILLING_REFUND_GRANT_INVARIANT_FAILURE",
                    record_ids=(grant.id,),
                    detail="A pending credit refund grant has a current subscription target.",
                )
            )
    subscriptions = list(db.scalars(select(Subscription).order_by(Subscription.id)))
    active_subscriptions_by_tenant: dict[str, list[str]] = defaultdict(list)
    for subscription in subscriptions:
        if subscription.status == "active":
            active_subscriptions_by_tenant[subscription.tenant_id].append(subscription.id)
    for subscription_ids in active_subscriptions_by_tenant.values():
        if len(subscription_ids) > 1:
            blockers.append(
                PricingReadinessBlocker(
                    code="BILLING_DUPLICATE_ACTIVE_SUBSCRIPTION",
                    record_ids=tuple(sorted(subscription_ids)),
                    detail="A tenant has more than one active subscription wallet.",
                )
            )
    for subscription in subscriptions:
        try:
            total = Decimal(str(subscription.quota_credits_total))
            used = Decimal(str(subscription.quota_credits_used))
            reserved = Decimal(str(subscription.quota_credits_reserved))
        except (ArithmeticError, ValueError, TypeError):
            total = used = reserved = Decimal("NaN")
        amounts_are_valid = all(
            value.is_finite() and value == value.to_integral_value()
            for value in (total, used, reserved)
        )
        if (
            not amounts_are_valid
            or total < 0
            or used < 0
            or reserved < 0
            or used + reserved > total
        ):
            blockers.append(
                PricingReadinessBlocker(
                    code="BILLING_WALLET_INVARIANT_FAILURE",
                    record_ids=(subscription.id,),
                    detail="Wallet totals, used credits, and reservations are inconsistent.",
                )
            )
        try:
            expected = brand_voice_orders._expected_reserved_credits(
                db, subscription_id=subscription.id
            )
        except (billing_operations.BillingInvariantError, ArithmeticError, ValueError):
            blockers.append(
                PricingReadinessBlocker(
                    code="BILLING_WALLET_INVARIANT_FAILURE",
                    record_ids=(subscription.id,),
                    detail="Reserved wallet reconciliation failed.",
                )
            )
            continue
        if amounts_are_valid and reserved != expected:
            blockers.append(
                PricingReadinessBlocker(
                    code="BILLING_WALLET_INVARIANT_FAILURE",
                    record_ids=(subscription.id,),
                    detail="Reserved wallet balance does not match active reservations.",
                )
            )
        if amounts_are_valid and used < settled_lower_bounds.get(subscription.id, 0):
            blockers.append(
                PricingReadinessBlocker(
                    code="BILLING_WALLET_INVARIANT_FAILURE",
                    record_ids=(subscription.id,),
                    detail="Used wallet balance is below linked operation settlements.",
                )
            )
    return blockers


def _legacy_slot_write_surfaces() -> tuple[LegacySlotWriteSurface, ...]:
    """Detect retired write endpoints without mistaking the GET inventory for one."""
    backend_root = Path(__file__).resolve().parents[2]
    route_source = (backend_root / "app" / "api" / "v1" / "routes" / "admin_console.py").read_text(
        encoding="utf-8"
    )
    surfaces: list[LegacySlotWriteSurface] = []
    if '@router.post(\n    "/tenants/{tenant_id}/voice-slots"' in route_source:
        state = "retired" if "VOICE_SLOT_ASSIGNMENT_RETIRED" in route_source else "active"
        surfaces.append(
            LegacySlotWriteSurface(
                surface="admin_api:POST /tenants/{tenant_id}/voice-slots",
                state=state,
            )
        )
    return tuple(surfaces)


def _inventory_blockers(
    db: Session,
    *,
    production_mode: bool,
) -> tuple[list[PricingReadinessBlocker], tuple[InventoryId, ...], dict[str, tuple[str, ...]]]:
    inventory = provider_voice_registry.provider_voice_inventory(db)
    unknown = tuple(InventoryId(provider_voice_id=value) for value in inventory.unknown_ids)
    blockers: list[PricingReadinessBlocker] = []
    if not production_mode:
        return blockers, unknown, _safe_inventory(inventory)
    if inventory.unknown_ids:
        blockers.append(
            PricingReadinessBlocker(
                code="UNKNOWN_HISTORIC_DOUBAO_ID",
                record_ids=tuple(inventory.unknown_ids),
                detail="Historic Doubao IDs must be classified before customer routing is enabled.",
            )
        )
    configured = set(inventory.official_configured_ids)
    registered = set(inventory.active_official_registry_ids)
    if not configured:
        blockers.append(
            PricingReadinessBlocker(
                code="OFFICIAL_DOUBAO_CONFIG_MISSING",
                record_ids=(),
                detail="No exact official Doubao voice list is configured.",
            )
        )
    elif configured != registered:
        blockers.append(
            PricingReadinessBlocker(
                code="OFFICIAL_DOUBAO_REGISTRY_MISMATCH",
                record_ids=tuple(sorted(configured | registered)),
                detail="Configured official IDs and registry rows differ.",
            )
        )
    customer_registry_ids = set(inventory.active_customer_registry_ids) | set(
        inventory.retired_customer_registry_ids
    )
    official_customer_conflicts = tuple(sorted(configured & customer_registry_ids))
    if official_customer_conflicts:
        blockers.append(
            PricingReadinessBlocker(
                code="OFFICIAL_DOUBAO_CUSTOMER_REGISTRY_CONFLICT",
                record_ids=official_customer_conflicts,
                detail="An official configured ID is already bound to a customer registry row.",
            )
        )
    if inventory.retired_official_registry_ids:
        blockers.append(
            PricingReadinessBlocker(
                code="RETIRED_OFFICIAL_DOUBAO_ID",
                record_ids=inventory.retired_official_registry_ids,
                detail="Official registry rows must remain active.",
            )
        )
    for row in inventory.registry_blockers:
        blockers.append(
            PricingReadinessBlocker(
                code="DOUBAO_REGISTRY_INVARIANT_FAILURE",
                record_ids=(row.row_id,),
                detail=row.reason,
            )
        )
    return blockers, unknown, _safe_inventory(inventory)


def _safe_inventory(inventory) -> dict[str, tuple[str, ...]]:
    return {
        "official_configured_ids": inventory.official_configured_ids,
        "legacy_configured_ids": inventory.legacy_configured_ids,
        "provider_config_ids": inventory.provider_config_ids,
        "brand_voice_ids": inventory.brand_voice_ids,
        "active_official_registry_ids": inventory.active_official_registry_ids,
        "retired_official_registry_ids": inventory.retired_official_registry_ids,
        "active_customer_registry_ids": inventory.active_customer_registry_ids,
        "retired_customer_registry_ids": inventory.retired_customer_registry_ids,
        "registry_blocker_ids": tuple(item.row_id for item in inventory.registry_blockers),
    }


def pricing_closure_readiness(
    db: Session,
    *,
    production_mode: bool,
) -> PricingReadinessReport:
    """Audit pricing closure without acquiring locks, mutating rows, or repairing data."""
    as_of = datetime.now(UTC)
    migration_revision = _migration_revision(db)
    if migration_revision != _TARGET_MIGRATION_REVISION:
        return _schema_not_ready_report(
            production_mode=production_mode,
            migration_revision=migration_revision,
            as_of=as_of,
        )
    schema_blockers = _target_schema_preflight_blockers(db)
    if _target_model_structure_is_missing(schema_blockers):
        return PricingReadinessReport(
            ready=False,
            production_mode=production_mode,
            migration_revision=migration_revision,
            generated_at=as_of,
            platform_rates=(),
            tenant_rates=(),
            platform_rate_ids=(),
            tenant_rate_ids=(),
            inventory_unknown_ids=(),
            provider_inventory={},
            legacy_slot_write_surfaces=(),
            blockers=tuple(schema_blockers),
        )
    rate_blockers, platform_rates, tenant_rates = _rate_blockers(db)
    inventory_blockers, unknown, inventory = _inventory_blockers(
        db, production_mode=production_mode
    )
    surfaces = _legacy_slot_write_surfaces()
    surface_blockers = [
        PricingReadinessBlocker(
            code="LEGACY_SLOT_WRITE_SURFACE",
            record_ids=(surface.surface,),
            detail="Legacy Doubao slot writing must be removed or explicitly retired.",
        )
        for surface in surfaces
        if surface.state != "retired"
    ]
    # Unknown IDs lead: operations tooling can safely make a deterministic first decision.
    blockers = tuple(
        schema_blockers
        + inventory_blockers
        + _production_environment_blockers(production_mode=production_mode)
        + _official_config_validation_blockers(production_mode=production_mode)
        + _policy_default_blockers()
        + rate_blockers
        + _billing_blockers(db, as_of=as_of)
        + surface_blockers
    )
    return PricingReadinessReport(
        ready=not blockers,
        production_mode=production_mode,
        migration_revision=migration_revision,
        generated_at=as_of,
        platform_rates=platform_rates,
        tenant_rates=tenant_rates,
        platform_rate_ids=tuple(item.id for item in platform_rates),
        tenant_rate_ids=tuple(item.id for item in tenant_rates),
        inventory_unknown_ids=unknown,
        provider_inventory=inventory,
        legacy_slot_write_surfaces=surfaces,
        blockers=blockers,
    )


def _redact_provider_id_blockers(payload: dict[str, object]) -> dict[str, object]:
    """Remove provider voice identifiers from a machine-readable CLI payload."""
    for blocker in payload["blockers"]:
        if blocker["code"] not in _PROVIDER_ID_BLOCKER_CODES:
            continue
        blocker["record_count"] = len(blocker["record_ids"])
        blocker["record_ids"] = []
    return payload


def _preflight_cli_payload(report: PricingPreflightReport) -> dict[str, object]:
    return _redact_provider_id_blockers(report.as_dict())


def _readiness_cli_payload(report: PricingReadinessReport) -> dict[str, object]:
    """Return operational evidence without disclosing provider voice identifiers."""
    payload = report.as_dict()
    payload["inventory_unknown_count"] = len(report.inventory_unknown_ids)
    payload["provider_inventory_counts"] = {
        key: len(value) for key, value in report.provider_inventory.items()
    }
    payload.pop("inventory_unknown_ids", None)
    payload.pop("provider_inventory", None)
    return _redact_provider_id_blockers(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pricing closure readiness gate")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=(
            "bootstrap-empty-preflight",
            "preflight",
            "audit",
            "register-official",
        ),
        default="audit",
        help=(
            "bootstrap-empty-preflight accepts only a database with no tables; "
            "preflight is legacy-schema-safe; audit requires the release schema; "
            "register-official registers only the configured exact list"
        ),
    )
    return parser


def _execute_cli_mode(db: Session, *, mode: str) -> tuple[int, dict[str, object]]:
    if mode == "bootstrap-empty-preflight":
        report = pricing_empty_bootstrap_preflight(db, production_mode=True)
        db.rollback()
        return (0 if report.ready else 2), _preflight_cli_payload(report)

    if mode == "preflight":
        report = pricing_closure_preflight(db, production_mode=True)
        db.rollback()
        return (0 if report.ready else 2), _preflight_cli_payload(report)

    if mode == "audit":
        report = pricing_closure_readiness(db, production_mode=True)
        db.rollback()
        return (0 if report.ready else 2), _readiness_cli_payload(report)

    migration_revision = _migration_revision(db)
    if migration_revision != _TARGET_MIGRATION_REVISION:
        db.rollback()
        return 2, {
            "error": "SCHEMA_NOT_READY",
            "migration_revision": migration_revision,
            "target_migration_revision": _TARGET_MIGRATION_REVISION,
        }

    configured_ids = tuple(settings.engine_doubao_official_voice_ids)
    provider_voice_registry.lock_provider_voice_registry_snapshot(
        db,
        provider_voice_ids=configured_ids,
    )
    # Re-read only after the global registry snapshot lock is held. The exact-list
    # writer takes the same lock and remains in this transaction.
    inventory = provider_voice_registry.provider_voice_inventory(db)
    readiness_report = pricing_closure_readiness(db, production_mode=True)
    blocker_codes = tuple(blocker.code for blocker in readiness_report.blockers)
    first_registration_ready = (
        blocker_codes == ("OFFICIAL_DOUBAO_REGISTRY_MISMATCH",)
        and not inventory.active_official_registry_ids
    )
    if readiness_report.ready:
        db.rollback()
        return 2, {"error": "OFFICIAL_REGISTRATION_ALREADY_COMPLETE"}
    if not first_registration_ready:
        db.rollback()
        return 2, {"error": "OFFICIAL_REGISTRATION_ABORTED"}
    provider_voice_registry.register_official_provider_voice_ids(
        db,
        provider_voice_ids=configured_ids,
    )
    db.commit()
    return 0, {"registered_official_count": len(inventory.official_configured_ids)}


def _quiet_rollback(db: Session | None) -> None:
    if db is None:
        return
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            db.rollback()
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    db: Session | None = None
    try:
        # Reserve both process streams for the single final JSON document. Shared
        # validators and database drivers may otherwise disclose exception details.
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            with SessionLocal() as db:
                exit_code, payload = _execute_cli_mode(db, mode=args.mode)
    except Exception:
        _quiet_rollback(db)
        error_code = (
            "OFFICIAL_REGISTRATION_FAILED"
            if args.mode == "register-official"
            else "PRICING_READINESS_FAILED"
        )
        exit_code, payload = 2, {"error": error_code}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
