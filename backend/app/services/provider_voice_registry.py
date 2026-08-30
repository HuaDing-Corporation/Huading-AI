from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import (
    BrandVoice,
    BrandVoiceOrder,
    BrandVoiceProviderId,
    ProviderConfig,
)

DOUBAO_VOICE_CLONE_PROVIDER = "doubao-voice-clone"
_REGISTRY_GLOBAL_LOCK_ID = "__registry_global__"


@dataclass(frozen=True)
class ProviderVoiceRegistryBlocker:
    row_id: str
    provider_voice_id: str
    provider: str
    kind: str
    status: str
    reason: str


@dataclass(frozen=True)
class ProviderVoiceInventory:
    official_configured_ids: tuple[str, ...]
    legacy_configured_ids: tuple[str, ...]
    provider_config_ids: tuple[str, ...]
    brand_voice_ids: tuple[str, ...]
    active_official_registry_ids: tuple[str, ...]
    retired_official_registry_ids: tuple[str, ...]
    active_customer_registry_ids: tuple[str, ...]
    retired_customer_registry_ids: tuple[str, ...]
    registry_blockers: tuple[ProviderVoiceRegistryBlocker, ...]
    unknown_ids: tuple[str, ...]


def normalize_provider_voice_id(value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise AppError(
            "Provider voice ID must not be empty.",
            code="PROVIDER_VOICE_ID_INVALID",
            status_code=422,
        )
    if len(normalized) > 160:
        raise AppError(
            "Provider voice ID is too long.",
            code="PROVIDER_VOICE_ID_INVALID",
            status_code=422,
        )
    return normalized


def provider_voice_lock_key(provider_voice_id: str) -> int:
    normalized = normalize_provider_voice_id(provider_voice_id)
    digest = hashlib.sha256(f"huading:voice-slot:{normalized}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _normalized_ids(raw_ids: object) -> tuple[str, ...]:
    if isinstance(raw_ids, str):
        candidates: object = raw_ids.split(",")
    elif isinstance(raw_ids, dict):
        candidates = raw_ids.keys()
    else:
        candidates = raw_ids or []
    normalized: set[str] = set()
    for candidate in candidates:  # type: ignore[union-attr]
        value = str(candidate).strip()
        if value:
            normalized.add(value)
    return tuple(sorted(normalized))


def lock_provider_voice_ids(
    db: Session,
    *,
    provider: str,
    provider_voice_ids: Sequence[str],
) -> None:
    del provider  # The compatibility lock namespace is intentionally provider-agnostic.
    normalized_ids = sorted({normalize_provider_voice_id(value) for value in provider_voice_ids})
    if db.get_bind().dialect.name != "postgresql":
        return
    for provider_voice_id in normalized_ids:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": provider_voice_lock_key(provider_voice_id)},
        )


def lock_provider_voice_registry_snapshot(
    db: Session,
    *,
    provider_voice_ids: Sequence[str] = (),
) -> None:
    """Serialize registry readers/writers, then lock dependent rows deterministically."""
    lock_provider_voice_ids(
        db,
        provider=DOUBAO_VOICE_CLONE_PROVIDER,
        provider_voice_ids=[_REGISTRY_GLOBAL_LOCK_ID],
    )
    lock_provider_voice_ids(
        db,
        provider=DOUBAO_VOICE_CLONE_PROVIDER,
        provider_voice_ids=provider_voice_ids,
    )
    list(
        db.scalars(
            select(BrandVoiceProviderId)
            .order_by(BrandVoiceProviderId.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    list(
        db.scalars(
            select(ProviderConfig)
            .order_by(ProviderConfig.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def _registry_rows(db: Session) -> list[BrandVoiceProviderId]:
    return list(db.scalars(select(BrandVoiceProviderId)))


def _customer_binding_reason(
    row: BrandVoiceProviderId,
    *,
    voice: BrandVoice,
    order: BrandVoiceOrder,
) -> str | None:
    if voice.provider != DOUBAO_VOICE_CLONE_PROVIDER:
        return "customer_voice_provider_not_canonical"
    if voice.owner_user_id is None:
        return "customer_voice_owner_missing"
    if voice.status != "ready":
        return "customer_voice_not_ready"
    if voice.tenant_id != order.tenant_id:
        return "customer_binding_tenant_mismatch"
    if voice.owner_user_id != order.user_id:
        return "customer_binding_user_mismatch"
    if order.status != "fulfilled":
        return "customer_order_not_fulfilled"
    if order.fulfilled_brand_voice_id != voice.id:
        return "customer_order_voice_mismatch"
    if order.fulfilled_provider_id != row.id:
        return "customer_order_provider_mismatch"
    return None


def _registry_blocker_reason(
    row: BrandVoiceProviderId,
    *,
    voices_by_id: dict[str, BrandVoice],
    orders_by_id: dict[str, BrandVoiceOrder],
    registry_rows: Sequence[BrandVoiceProviderId],
) -> str | None:
    raw_provider_id = str(row.normalized_provider_id)
    if not raw_provider_id.strip() or raw_provider_id != raw_provider_id.strip():
        return "provider_voice_id_not_normalized"
    if row.provider != DOUBAO_VOICE_CLONE_PROVIDER:
        return "provider_not_canonical"
    if row.kind == "official":
        if row.status != "active":
            return "official_status_not_active"
        if row.brand_voice_id is not None or row.first_order_id is not None:
            return "official_has_customer_binding"
        return None
    if row.kind != "customer":
        return "kind_invalid"
    if row.status not in {"active", "retired"}:
        return "customer_status_invalid"
    if row.brand_voice_id is None or row.first_order_id is None:
        return "customer_binding_missing"
    voice = voices_by_id.get(row.brand_voice_id)
    order = orders_by_id.get(row.first_order_id)
    if voice is None or order is None:
        return "customer_binding_target_missing"
    binding_reason = _customer_binding_reason(row, voice=voice, order=order)
    if binding_reason is not None:
        return binding_reason
    current_provider_id = str(voice.speaker_id or "")
    if row.status == "active":
        if current_provider_id != row.normalized_provider_id:
            return "active_customer_speaker_mismatch"
        return None
    if current_provider_id == row.normalized_provider_id:
        return "retired_customer_still_current"
    for active_row in registry_rows:
        if (
            active_row.id == row.id
            or active_row.provider != DOUBAO_VOICE_CLONE_PROVIDER
            or active_row.kind != "customer"
            or active_row.status != "active"
            or active_row.brand_voice_id != voice.id
            or active_row.normalized_provider_id != current_provider_id
            or active_row.first_order_id is None
        ):
            continue
        active_order = orders_by_id.get(active_row.first_order_id)
        if active_order is not None and _customer_binding_reason(
            active_row,
            voice=voice,
            order=active_order,
        ) is None:
            return None
    return "retired_customer_current_binding_missing"


def provider_voice_inventory(db: Session) -> ProviderVoiceInventory:
    official = _normalized_ids(getattr(settings, "engine_doubao_official_voice_ids", []))
    legacy = _normalized_ids(getattr(settings, "engine_doubao_voice_clone_speaker_ids", []))
    config_ids: set[str] = set()
    for config in db.scalars(select(ProviderConfig)):
        values = dict(config.config or {})
        config_ids.update(_normalized_ids(values.get("speaker_ids")))
        config_ids.update(_normalized_ids(values.get("used_speaker_ids")))
    voices = list(db.scalars(select(BrandVoice)))
    voices_by_id = {voice.id: voice for voice in voices}
    orders_by_id = {order.id: order for order in db.scalars(select(BrandVoiceOrder))}
    brand_voice_ids = {
        normalized
        for voice in voices
        if voice.provider == DOUBAO_VOICE_CLONE_PROVIDER
        if voice.speaker_id is not None
        if (normalized := str(voice.speaker_id).strip())
    }
    registry_groups: dict[tuple[str, str], set[str]] = {
        (kind, status): set()
        for kind in ("official", "customer")
        for status in ("active", "retired")
    }
    registry_blockers: list[ProviderVoiceRegistryBlocker] = []
    registry_rows = _registry_rows(db)
    for row in registry_rows:
        reason = _registry_blocker_reason(
            row,
            voices_by_id=voices_by_id,
            orders_by_id=orders_by_id,
            registry_rows=registry_rows,
        )
        if reason is not None:
            registry_blockers.append(
                ProviderVoiceRegistryBlocker(
                    row_id=row.id,
                    provider_voice_id=str(row.normalized_provider_id),
                    provider=str(row.provider),
                    kind=str(row.kind),
                    status=str(row.status),
                    reason=reason,
                )
            )
            continue
        registry_groups[(row.kind, row.status)].add(row.normalized_provider_id)
    official_registry = registry_groups[("official", "active")]
    customer_registry = (
        registry_groups[("customer", "active")] | registry_groups[("customer", "retired")]
    )
    historical = set(legacy) | config_ids | brand_voice_ids
    unknown = historical - set(official) - customer_registry
    return ProviderVoiceInventory(
        official_configured_ids=official,
        legacy_configured_ids=legacy,
        provider_config_ids=tuple(sorted(config_ids)),
        brand_voice_ids=tuple(sorted(brand_voice_ids)),
        active_official_registry_ids=tuple(sorted(official_registry)),
        retired_official_registry_ids=tuple(sorted(registry_groups[("official", "retired")])),
        active_customer_registry_ids=tuple(sorted(registry_groups[("customer", "active")])),
        retired_customer_registry_ids=tuple(sorted(registry_groups[("customer", "retired")])),
        registry_blockers=tuple(
            sorted(
                registry_blockers,
                key=lambda item: (item.provider_voice_id, item.row_id),
            )
        ),
        unknown_ids=tuple(sorted(unknown)),
    )


def _conflict(provider_voice_id: str) -> AppError:
    return AppError(
        f"Provider voice ID is already occupied: {provider_voice_id}",
        code="PROVIDER_VOICE_ID_CONFLICT",
        status_code=409,
    )


def _historical_ids(db: Session) -> set[str]:
    inventory = provider_voice_inventory(db)
    return (
        set(inventory.official_configured_ids)
        | set(inventory.legacy_configured_ids)
        | set(inventory.provider_config_ids)
        | set(inventory.brand_voice_ids)
    )


def _registry_rows_for_update(
    db: Session,
    *,
    provider_voice_id: str,
) -> list[BrandVoiceProviderId]:
    return list(
        db.scalars(
            select(BrandVoiceProviderId)
            .where(
                func.trim(BrandVoiceProviderId.normalized_provider_id) == provider_voice_id,
            )
            .with_for_update()
        )
    )


def _is_valid_customer_row(db: Session, row: BrandVoiceProviderId) -> bool:
    if row.brand_voice_id is None or row.first_order_id is None:
        return False
    voice = db.get(BrandVoice, row.brand_voice_id)
    order = db.get(BrandVoiceOrder, row.first_order_id)
    if voice is None or order is None:
        return False
    orders_by_id = {
        candidate.id: candidate for candidate in db.scalars(select(BrandVoiceOrder))
    }
    return (
        _registry_blocker_reason(
            row,
            voices_by_id={voice.id: voice},
            orders_by_id=orders_by_id,
            registry_rows=_registry_rows(db),
        )
        is None
    )


def _same_voice_renewal_has_other_source(
    db: Session,
    *,
    provider_voice_id: str,
    brand_voice_id: str,
) -> bool:
    target_voice = db.scalar(select(BrandVoice).where(BrandVoice.id == brand_voice_id))
    if target_voice is None or target_voice.provider != DOUBAO_VOICE_CLONE_PROVIDER:
        return True
    raw_target_provider_id = target_voice.speaker_id
    if (
        raw_target_provider_id is None
        or raw_target_provider_id != raw_target_provider_id.strip()
        or raw_target_provider_id != provider_voice_id
    ):
        return True
    inventory = provider_voice_inventory(db)
    if provider_voice_id in (
        set(inventory.official_configured_ids)
        | set(inventory.legacy_configured_ids)
        | set(inventory.provider_config_ids)
    ):
        return True
    matching_voices = [
        voice
        for voice in db.scalars(select(BrandVoice).where(BrandVoice.speaker_id.is_not(None)))
        if str(voice.speaker_id).strip() == provider_voice_id
    ]
    return any(voice.id != brand_voice_id for voice in matching_voices)


def claim_customer_provider_voice_id(
    db: Session,
    *,
    provider_voice_id: str,
    brand_voice_id: str,
    order_id: str,
    previous_provider_voice_id: str | None = None,
    flush: bool = True,
) -> BrandVoiceProviderId:
    normalized = normalize_provider_voice_id(provider_voice_id)
    previous = (
        normalize_provider_voice_id(previous_provider_voice_id)
        if previous_provider_voice_id is not None
        else None
    )
    locked_ids = tuple(sorted({normalized, *([previous] if previous else [])}))
    lock_provider_voice_registry_snapshot(
        db,
        provider_voice_ids=locked_ids,
    )

    previous_row: BrandVoiceProviderId | None = None
    if previous is not None and previous != normalized:
        previous_rows = _registry_rows_for_update(db, provider_voice_id=previous)
        previous_row = previous_rows[0] if len(previous_rows) == 1 else None
        if (
            previous_row is None
            or previous_row.provider != DOUBAO_VOICE_CLONE_PROVIDER
            or previous_row.kind != "customer"
            or previous_row.status != "active"
            or previous_row.brand_voice_id != brand_voice_id
        ):
            raise _conflict(previous)

    existing_rows = _registry_rows_for_update(db, provider_voice_id=normalized)
    if len(existing_rows) > 1:
        raise _conflict(normalized)
    existing = existing_rows[0] if existing_rows else None
    if existing is not None:
        if (
            _is_valid_customer_row(db, existing)
            and existing.status == "active"
            and existing.brand_voice_id == brand_voice_id
            and not _same_voice_renewal_has_other_source(
                db,
                provider_voice_id=normalized,
                brand_voice_id=brand_voice_id,
            )
        ):
            claimed = existing
        else:
            raise _conflict(normalized)
    else:
        if normalized in _historical_ids(db):
            raise _conflict(normalized)
        claimed = BrandVoiceProviderId(
            id=str(uuid4()),
            provider=DOUBAO_VOICE_CLONE_PROVIDER,
            normalized_provider_id=normalized,
            kind="customer",
            brand_voice_id=brand_voice_id,
            first_order_id=order_id,
            status="active",
        )
        db.add(claimed)

    if previous_row is not None:
        previous_row.status = "retired"
        previous_row.updated_at = datetime.now(UTC)
    if flush:
        db.flush()
    return claimed


def retire_customer_provider_voice_id(
    db: Session,
    *,
    brand_voice_id: str,
    provider_voice_id: str,
    retired_at: datetime,
) -> BrandVoiceProviderId:
    normalized = normalize_provider_voice_id(provider_voice_id)
    lock_provider_voice_registry_snapshot(
        db,
        provider_voice_ids=[normalized],
    )
    rows = _registry_rows_for_update(db, provider_voice_id=normalized)
    row = rows[0] if len(rows) == 1 else None
    if (
        row is None
        or row.provider != DOUBAO_VOICE_CLONE_PROVIDER
        or row.kind != "customer"
        or row.brand_voice_id != brand_voice_id
        or row.status != "active"
    ):
        raise _conflict(normalized)
    row.status = "retired"
    row.updated_at = retired_at
    db.flush()
    return row


def register_official_provider_voice_ids(
    db: Session,
    *,
    provider_voice_ids: Sequence[str],
) -> list[BrandVoiceProviderId]:
    requested = tuple(sorted({normalize_provider_voice_id(value) for value in provider_voice_ids}))
    configured = _normalized_ids(getattr(settings, "engine_doubao_official_voice_ids", []))
    if not configured or requested != configured:
        raise AppError(
            "Official provider voice IDs must exactly match the configured signed list.",
            code="OFFICIAL_PROVIDER_VOICE_IDS_MISMATCH",
            status_code=422,
        )
    lock_provider_voice_registry_snapshot(
        db,
        provider_voice_ids=requested,
    )
    rows: list[BrandVoiceProviderId] = []
    for provider_voice_id in requested:
        existing_rows = _registry_rows_for_update(
            db,
            provider_voice_id=provider_voice_id,
        )
        if len(existing_rows) > 1:
            raise _conflict(provider_voice_id)
        existing = existing_rows[0] if existing_rows else None
        if existing is None:
            existing = BrandVoiceProviderId(
                provider=DOUBAO_VOICE_CLONE_PROVIDER,
                normalized_provider_id=provider_voice_id,
                kind="official",
                status="active",
            )
            db.add(existing)
        elif (
            existing.provider != DOUBAO_VOICE_CLONE_PROVIDER
            or existing.kind != "official"
            or existing.status != "active"
        ):
            raise _conflict(provider_voice_id)
        rows.append(existing)
    db.flush()
    return rows


def assert_doubao_registry_ready(
    db: Session,
    *,
    provider_voice_ids: Sequence[str] = (),
) -> None:
    if str(getattr(settings, "environment", "")).strip().lower() not in {
        "prod",
        "production",
    }:
        return
    lock_provider_voice_registry_snapshot(
        db,
        provider_voice_ids=provider_voice_ids,
    )
    inventory = provider_voice_inventory(db)
    configured = set(inventory.official_configured_ids)
    registered = set(inventory.active_official_registry_ids)
    if (
        not configured
        or configured != registered
        or inventory.retired_official_registry_ids
        or inventory.registry_blockers
        or inventory.unknown_ids
    ):
        raise AppError(
            "Doubao provider voice registry is not ready.",
            code="DOUBAO_REGISTRY_NOT_READY",
            status_code=503,
            detail={
                "official_configured_ids": sorted(configured),
                "official_registered_ids": sorted(registered),
                "registry_blockers": [
                    {
                        "provider_voice_id": item.provider_voice_id,
                        "provider": item.provider,
                        "kind": item.kind,
                        "status": item.status,
                        "reason": item.reason,
                    }
                    for item in inventory.registry_blockers
                ],
                "unknown_ids": list(inventory.unknown_ids),
            },
            expose_detail=False,
        )
