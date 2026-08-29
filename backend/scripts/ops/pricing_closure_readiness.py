from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import BillingOperation, CreditRate, Subscription, UsageRecord
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


def _migration_revision(db: Session) -> str | None:
    return MigrationContext.configure(db.connection()).get_current_revision()


def _rate_blockers(
    db: Session,
) -> tuple[
    list[PricingReadinessBlocker],
    tuple[RateRow, ...],
    tuple[RateRow, ...],
]:
    rows = list(db.scalars(select(CreditRate).order_by(CreditRate.id)))
    blockers: list[PricingReadinessBlocker] = []
    for row in rows:
        try:
            validate_credit_rate_candidate(
                tenant_id=row.tenant_id,
                capability=row.capability,
                unit=row.unit,
                credits_per_unit=Decimal(row.credits_per_unit),
                is_active=bool(row.is_active),
            )
        except (PricingInvariantError, ArithmeticError, ValueError) as exc:
            blockers.append(
                PricingReadinessBlocker(
                    code="INVALID_CREDIT_RATE",
                    record_ids=(row.id,),
                    detail=str(exc),
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
    safe_rows = tuple(
        RateRow(
            id=row.id,
            scope="platform" if row.tenant_id is None else "tenant",
            tenant_id=row.tenant_id,
            capability=row.capability,
            unit=row.unit,
            credits_per_unit=format(Decimal(row.credits_per_unit), "f"),
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


def _billing_blockers(db: Session) -> list[PricingReadinessBlocker]:
    blockers: list[PricingReadinessBlocker] = []
    operations = list(db.scalars(select(BillingOperation).order_by(BillingOperation.id)))
    for operation in operations:
        try:
            billing_operations.billing_summary(operation)
            snapshot = billing_operations._snapshot_from_operation(operation)
            usages = list(
                db.scalars(
                    select(UsageRecord)
                    .where(UsageRecord.billing_operation_id == operation.id)
                    .order_by(UsageRecord.billing_item_index)
                )
            )
            billing_operations._validate_lookup_usages(operation, snapshot, usages)
            if operation.status == "completed" and operation.completion_kind == "succeeded":
                billing_operations._validate_stored_result(operation)
            elif operation.status == "completed" and operation.completion_kind in {
                "failed",
                "rejected",
            }:
                billing_operations._validate_stored_error_payload(operation)
        except (billing_operations.BillingInvariantError, ValueError, TypeError) as exc:
            blockers.append(
                PricingReadinessBlocker(
                    code="BILLING_OPERATION_INVARIANT_FAILURE",
                    record_ids=(operation.id,),
                    detail=str(exc),
                )
            )
    for subscription in db.scalars(select(Subscription).order_by(Subscription.id)):
        try:
            expected = brand_voice_orders._expected_reserved_credits(
                db, subscription_id=subscription.id
            )
        except (billing_operations.BillingInvariantError, ArithmeticError, ValueError) as exc:
            blockers.append(
                PricingReadinessBlocker(
                    code="BILLING_WALLET_INVARIANT_FAILURE",
                    record_ids=(subscription.id,),
                    detail=str(exc),
                )
            )
            continue
        if subscription.quota_credits_reserved != expected:
            blockers.append(
                PricingReadinessBlocker(
                    code="BILLING_WALLET_INVARIANT_FAILURE",
                    record_ids=(subscription.id,),
                    detail="Reserved wallet balance does not match active reservations.",
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
        state = (
            "retired"
            if "VOICE_SLOT_ASSIGNMENT_RETIRED" in route_source
            else "active"
        )
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
        inventory_blockers
        + _policy_default_blockers()
        + rate_blockers
        + _billing_blockers(db)
        + surface_blockers
    )
    return PricingReadinessReport(
        ready=not blockers,
        production_mode=production_mode,
        migration_revision=_migration_revision(db),
        generated_at=datetime.now(UTC),
        platform_rates=platform_rates,
        tenant_rates=tenant_rates,
        platform_rate_ids=tuple(item.id for item in platform_rates),
        tenant_rate_ids=tuple(item.id for item in tenant_rates),
        inventory_unknown_ids=unknown,
        provider_inventory=inventory,
        legacy_slot_write_surfaces=surfaces,
        blockers=blockers,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pricing closure readiness gate")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("audit", "register-official"),
        default="audit",
        help="audit is read-only; register-official registers only the configured exact list",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with SessionLocal() as db:
        if args.mode == "audit":
            report = pricing_closure_readiness(db, production_mode=True)
            print(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
            db.rollback()
            return 0 if report.ready else 2

        configured_ids = tuple(settings.engine_doubao_official_voice_ids)
        provider_voice_registry.lock_provider_voice_registry_snapshot(
            db,
            provider_voice_ids=configured_ids,
        )
        # Re-read only after the global registry snapshot lock is held.  The
        # exact-list writer takes the same lock and remains in this transaction.
        inventory = provider_voice_registry.provider_voice_inventory(db)
        if inventory.unknown_ids or inventory.registry_blockers:
            print(
                json.dumps(
                    {
                        "error": (
                            "official registration aborted: unknown or invalid provider inventory"
                        ),
                        "unknown_ids": list(inventory.unknown_ids),
                        "registry_blocker_ids": [
                            item.row_id for item in inventory.registry_blockers
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            db.rollback()
            return 2
        provider_voice_registry.register_official_provider_voice_ids(
            db,
            provider_voice_ids=configured_ids,
        )
        db.commit()
        print(json.dumps({"registered_official_ids": list(inventory.official_configured_ids)}))
        return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
