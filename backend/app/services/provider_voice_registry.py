from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import BrandVoice, BrandVoiceProviderId, ProviderConfig

DOUBAO_VOICE_CLONE_PROVIDER = "doubao-voice-clone"


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
    normalized_ids = sorted(
        {normalize_provider_voice_id(value) for value in provider_voice_ids}
    )
    if db.get_bind().dialect.name != "postgresql":
        return
    for provider_voice_id in normalized_ids:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": provider_voice_lock_key(provider_voice_id)},
        )


def _registry_rows(db: Session) -> list[BrandVoiceProviderId]:
    return list(
        db.scalars(
            select(BrandVoiceProviderId).where(
                BrandVoiceProviderId.provider == DOUBAO_VOICE_CLONE_PROVIDER
            )
        )
    )


def provider_voice_inventory(db: Session) -> ProviderVoiceInventory:
    official = _normalized_ids(
        getattr(settings, "engine_doubao_official_voice_ids", [])
    )
    legacy = _normalized_ids(
        getattr(settings, "engine_doubao_voice_clone_speaker_ids", [])
    )
    config_ids: set[str] = set()
    for config in db.scalars(select(ProviderConfig)):
        values = dict(config.config or {})
        config_ids.update(_normalized_ids(values.get("speaker_ids")))
        config_ids.update(_normalized_ids(values.get("used_speaker_ids")))
    brand_voice_ids = {
        normalized
        for value in db.scalars(
            select(BrandVoice.speaker_id).where(BrandVoice.speaker_id.is_not(None))
        )
        if (normalized := str(value).strip())
    }
    registry_groups: dict[tuple[str, str], set[str]] = {
        (kind, status): set()
        for kind in ("official", "customer")
        for status in ("active", "retired")
    }
    for row in _registry_rows(db):
        registry_groups[(row.kind, row.status)].add(row.normalized_provider_id)
    official_registry = registry_groups[("official", "active")]
    customer_registry = (
        registry_groups[("customer", "active")]
        | registry_groups[("customer", "retired")]
    )
    historical = set(legacy) | config_ids | brand_voice_ids
    unknown = historical - set(official) - customer_registry
    return ProviderVoiceInventory(
        official_configured_ids=official,
        legacy_configured_ids=legacy,
        provider_config_ids=tuple(sorted(config_ids)),
        brand_voice_ids=tuple(sorted(brand_voice_ids)),
        active_official_registry_ids=tuple(sorted(official_registry)),
        retired_official_registry_ids=tuple(
            sorted(registry_groups[("official", "retired")])
        ),
        active_customer_registry_ids=tuple(
            sorted(registry_groups[("customer", "active")])
        ),
        retired_customer_registry_ids=tuple(
            sorted(registry_groups[("customer", "retired")])
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


def _customer_row_for_update(
    db: Session,
    *,
    provider_voice_id: str,
) -> BrandVoiceProviderId | None:
    return db.scalar(
        select(BrandVoiceProviderId)
        .where(
            BrandVoiceProviderId.normalized_provider_id == provider_voice_id,
        )
        .with_for_update()
    )


def claim_customer_provider_voice_id(
    db: Session,
    *,
    provider_voice_id: str,
    brand_voice_id: str,
    order_id: str,
    previous_provider_voice_id: str | None = None,
) -> BrandVoiceProviderId:
    normalized = normalize_provider_voice_id(provider_voice_id)
    previous = (
        normalize_provider_voice_id(previous_provider_voice_id)
        if previous_provider_voice_id is not None
        else None
    )
    locked_ids = tuple(sorted({normalized, *([previous] if previous else [])}))
    lock_provider_voice_ids(
        db,
        provider=DOUBAO_VOICE_CLONE_PROVIDER,
        provider_voice_ids=locked_ids,
    )

    previous_row: BrandVoiceProviderId | None = None
    if previous is not None and previous != normalized:
        previous_row = _customer_row_for_update(db, provider_voice_id=previous)
        if (
            previous_row is None
            or previous_row.provider != DOUBAO_VOICE_CLONE_PROVIDER
            or previous_row.kind != "customer"
            or previous_row.status != "active"
            or previous_row.brand_voice_id != brand_voice_id
        ):
            raise _conflict(previous)

    existing = _customer_row_for_update(db, provider_voice_id=normalized)
    if existing is not None:
        if (
            existing.provider == DOUBAO_VOICE_CLONE_PROVIDER
            and existing.kind == "customer"
            and existing.status == "active"
            and existing.brand_voice_id == brand_voice_id
        ):
            claimed = existing
        else:
            raise _conflict(normalized)
    else:
        if normalized in _historical_ids(db):
            raise _conflict(normalized)
        claimed = BrandVoiceProviderId(
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
    lock_provider_voice_ids(
        db,
        provider=DOUBAO_VOICE_CLONE_PROVIDER,
        provider_voice_ids=[normalized],
    )
    row = _customer_row_for_update(db, provider_voice_id=normalized)
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
    requested = tuple(
        sorted({normalize_provider_voice_id(value) for value in provider_voice_ids})
    )
    configured = _normalized_ids(
        getattr(settings, "engine_doubao_official_voice_ids", [])
    )
    if not configured or requested != configured:
        raise AppError(
            "Official provider voice IDs must exactly match the configured signed list.",
            code="OFFICIAL_PROVIDER_VOICE_IDS_MISMATCH",
            status_code=422,
        )
    lock_provider_voice_ids(
        db,
        provider=DOUBAO_VOICE_CLONE_PROVIDER,
        provider_voice_ids=requested,
    )
    rows: list[BrandVoiceProviderId] = []
    for provider_voice_id in requested:
        existing = _customer_row_for_update(db, provider_voice_id=provider_voice_id)
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


def assert_doubao_registry_ready(db: Session) -> None:
    if str(getattr(settings, "environment", "")).strip().lower() not in {
        "prod",
        "production",
    }:
        return
    inventory = provider_voice_inventory(db)
    configured = set(inventory.official_configured_ids)
    registered = set(inventory.active_official_registry_ids)
    if (
        not configured
        or configured != registered
        or inventory.retired_official_registry_ids
        or inventory.unknown_ids
    ):
        raise AppError(
            "Doubao provider voice registry is not ready.",
            code="DOUBAO_REGISTRY_NOT_READY",
            status_code=503,
            detail={
                "official_configured_ids": sorted(configured),
                "official_registered_ids": sorted(registered),
                "unknown_ids": list(inventory.unknown_ids),
            },
        )
