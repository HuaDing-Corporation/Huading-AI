from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import BillingOperation

_RECOVERY_MARKER_PATTERN = re.compile(r"^[a-z0-9]{9}$")


class CosyVoiceRecoveryMarkerCollisionError(RuntimeError):
    pass


class CosyVoiceRecoveryInvariantError(RuntimeError):
    pass


def _cosyvoice_recovery_lock_key(marker: str) -> int:
    if not _RECOVERY_MARKER_PATTERN.fullmatch(marker):
        raise ValueError(
            "CosyVoice recovery marker must be nine lowercase alphanumeric characters."
        )
    digest = hashlib.sha256(f"huading:cosyvoice-recovery:{marker}".encode("ascii")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def lock_cosyvoice_recovery_marker(db: Session, *, marker: str) -> None:
    lock_id = _cosyvoice_recovery_lock_key(marker)
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": lock_id},
    )


def assert_cosyvoice_recovery_marker_owned(
    *,
    request_key: str,
    marker: str,
    candidate_request_keys: Iterable[str],
    marker_for_request: Callable[[str], str],
) -> None:
    for candidate_request_key in candidate_request_keys:
        if candidate_request_key == request_key:
            continue
        if marker_for_request(candidate_request_key) == marker:
            raise CosyVoiceRecoveryMarkerCollisionError(
                "CosyVoice recovery marker is reserved by a different request."
            )


@contextmanager
def claim_cosyvoice_recovery_marker(
    *,
    session_factory: Callable[[], Session],
    operation_id: str,
    request_key: str,
    marker: str,
    marker_for_request: Callable[[str], str],
) -> Iterator[None]:
    with session_factory() as db:
        try:
            lock_cosyvoice_recovery_marker(db, marker=marker)
            stored_request_key = db.scalar(
                select(BillingOperation.request_hash).where(
                    BillingOperation.id == operation_id,
                    BillingOperation.operation == "cosyvoice_brand_voice_create",
                )
            )
            if stored_request_key != request_key:
                raise CosyVoiceRecoveryInvariantError(
                    "CosyVoice recovery operation does not match its request key."
                )
            candidate_request_keys = db.scalars(
                select(BillingOperation.request_hash).where(
                    BillingOperation.operation == "cosyvoice_brand_voice_create"
                )
            )
            assert_cosyvoice_recovery_marker_owned(
                request_key=request_key,
                marker=marker,
                candidate_request_keys=candidate_request_keys,
                marker_for_request=marker_for_request,
            )
            yield
        finally:
            db.rollback()
