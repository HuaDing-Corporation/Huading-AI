from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import ProviderConfig, Tenant

DOUBAO_VOICE_CLONE_PROVIDER = "doubao-voice-clone"
SPEAKER_ID_PATTERN = re.compile(r"^S_[A-Za-z0-9_-]{1,157}$")


class SpeakerSlotAssignmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeakerSlotAssignmentSummary:
    apply: bool
    tenant_id: str
    tenant_slug: str
    speaker_id: str
    config_created: bool
    changed: bool
    previous_speaker_ids: tuple[str, ...]
    speaker_ids: tuple[str, ...]


def speaker_ids(raw_ids: object) -> list[str]:
    candidates = raw_ids.split(",") if isinstance(raw_ids, str) else raw_ids or []
    return list(dict.fromkeys(str(item).strip() for item in candidates if str(item).strip()))


def assign_speaker_slot(
    db: Session,
    *,
    tenant_slug: str,
    speaker_id: str,
    apply: bool = False,
    platform_speaker_ids: object | None = None,
) -> SpeakerSlotAssignmentSummary:
    slug = tenant_slug.strip()
    normalized_speaker_id = speaker_id.strip()
    if not slug:
        raise SpeakerSlotAssignmentError("Tenant slug must not be empty.")
    if not SPEAKER_ID_PATTERN.fullmatch(normalized_speaker_id):
        raise SpeakerSlotAssignmentError("Speaker ID must match S_[A-Za-z0-9_-]+.")

    _lock_speaker_slot(db, normalized_speaker_id)
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        raise SpeakerSlotAssignmentError("Target tenant was not found.")

    configs = list(
        db.scalars(
            select(ProviderConfig)
            .where(
                ProviderConfig.capability == "voice_clone",
                ProviderConfig.provider == DOUBAO_VOICE_CLONE_PROVIDER,
            )
            .with_for_update()
        )
    )
    tenant_config = next((item for item in configs if item.tenant_id == tenant.id), None)
    for config in configs:
        if config is tenant_config:
            continue
        values = dict(config.config or {})
        configured = set(speaker_ids(values.get("speaker_ids")))
        used = set(speaker_ids(values.get("used_speaker_ids")))
        if normalized_speaker_id in configured or normalized_speaker_id in used:
            raise SpeakerSlotAssignmentError(
                "Speaker ID is already assigned to another tenant or the platform pool."
            )

    active_platform_config = next(
        (item for item in configs if item.tenant_id is None and item.is_active),
        None,
    )
    platform_values = (
        dict(active_platform_config.config or {})
        if active_platform_config is not None
        else {}
    )
    env_ids = (
        settings.engine_doubao_voice_clone_speaker_ids
        if platform_speaker_ids is None
        else platform_speaker_ids
    )
    if (
        "speaker_ids" not in platform_values
        and normalized_speaker_id in speaker_ids(env_ids)
    ):
        raise SpeakerSlotAssignmentError(
            "Speaker ID is already assigned to another tenant or the platform pool."
        )

    values = dict(tenant_config.config or {}) if tenant_config is not None else {}
    assigned_ids = speaker_ids(values.get("speaker_ids"))
    previous_speaker_ids = tuple(assigned_ids)
    slot_added = normalized_speaker_id not in assigned_ids
    if slot_added:
        assigned_ids.append(normalized_speaker_id)
    config_created = tenant_config is None
    changed = config_created or slot_added or not bool(tenant_config.is_active)

    if apply and changed:
        values["speaker_ids"] = assigned_ids
        if tenant_config is None:
            tenant_config = ProviderConfig(
                tenant_id=tenant.id,
                capability="voice_clone",
                provider=DOUBAO_VOICE_CLONE_PROVIDER,
                config=values,
                is_active=True,
            )
            db.add(tenant_config)
        else:
            tenant_config.config = values
            tenant_config.is_active = True
        db.flush()

    return SpeakerSlotAssignmentSummary(
        apply=apply,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        speaker_id=normalized_speaker_id,
        config_created=config_created,
        changed=changed,
        previous_speaker_ids=previous_speaker_ids,
        speaker_ids=tuple(assigned_ids),
    )


def _speaker_slot_lock_id(speaker_id: str) -> int:
    digest = hashlib.sha256(f"huading:voice-slot:{speaker_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _lock_speaker_slot(db: Session, speaker_id: str) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _speaker_slot_lock_id(speaker_id)},
    )
