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
from app.services import billing_operations, brand_voice_orders, provider_voice_registry
from app.services.pricing import PricingInvariantError, validate_credit_rate_candidate


@dataclass(frozen=True)
class PricingReadinessBlocker:
    code: str
    record_ids: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class InventoryId:
    provider_voice_id: str


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
    platform_rate_ids: tuple[str, ...]
    tenant_rate_ids: tuple[str, ...]
    inventory_unknown_ids: tuple[InventoryId, ...]
    legacy_slot_write_surfaces: tuple[LegacySlotWriteSurface, ...]
    blockers: tuple[PricingReadinessBlocker, ...]

    def as_dict(self) -> dict[str, object]:
        """Return a machine-readable report containing IDs, never credentials or URLs."""
        return {
            "ready": self.ready,
            "production_mode": self.production_mode,
            "migration_revision": self.migration_revision,
            "generated_at": self.generated_at.isoformat(),
            "platform_rate_ids": list(self.platform_rate_ids),
            "tenant_rate_ids": list(self.tenant_rate_ids),
            "inventory_unknown_ids": [asdict(item) for item in self.inventory_unknown_ids],
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
) -> tuple[list[PricingReadinessBlocker], tuple[str, ...], tuple[str, ...]]:
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
    return (
        blockers,
        tuple(row.id for row in rows if row.tenant_id is None),
        tuple(row.id for row in rows if row.tenant_id is not None),
    )


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
) -> tuple[list[PricingReadinessBlocker], tuple[InventoryId, ...]]:
    inventory = provider_voice_registry.provider_voice_inventory(db)
    unknown = tuple(InventoryId(provider_voice_id=value) for value in inventory.unknown_ids)
    blockers: list[PricingReadinessBlocker] = []
    if not production_mode:
        return blockers, unknown
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
    return blockers, unknown


def pricing_closure_readiness(
    db: Session,
    *,
    production_mode: bool,
) -> PricingReadinessReport:
    """Audit pricing closure without acquiring locks, mutating rows, or repairing data."""
    rate_blockers, platform_rate_ids, tenant_rate_ids = _rate_blockers(db)
    inventory_blockers, unknown = _inventory_blockers(db, production_mode=production_mode)
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
        + rate_blockers
        + _billing_blockers(db)
        + surface_blockers
    )
    return PricingReadinessReport(
        ready=not blockers,
        production_mode=production_mode,
        migration_revision=_migration_revision(db),
        generated_at=datetime.now(UTC),
        platform_rate_ids=platform_rate_ids,
        tenant_rate_ids=tenant_rate_ids,
        inventory_unknown_ids=unknown,
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
            provider_voice_ids=settings.engine_doubao_official_voice_ids,
        )
        db.commit()
        print(json.dumps({"registered_official_ids": list(inventory.official_configured_ids)}))
        return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
