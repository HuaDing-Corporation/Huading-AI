from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GcCandidate, GcCandidateStatus, GcSkipReason, Tenant
from app.services.gc.reference_scan import StorageKeyScope, classify_storage_key

MIN_FIRST_SEEN_AGE = timedelta(days=7)
MIN_CLEAN_SCAN_INTERVAL = timedelta(hours=24)


@dataclass(frozen=True)
class GcObjectObservation:
    tenant_id: str
    key: str
    size: int
    last_modified: datetime
    version_id: str | None
    etag: str | None
    referenced: bool
    reference_evidence: dict[str, object]


@dataclass(frozen=True)
class GcObservationResult:
    observed_count: int
    eligible_count: int
    skipped_count: int


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _evidence(
    observation: GcObjectObservation,
    *,
    scan_id: str,
    schema_fingerprint: str,
    scanned_at: datetime,
) -> dict[str, object]:
    return {
        **observation.reference_evidence,
        "scan_id": scan_id,
        "scanned_at": scanned_at.isoformat(),
        "schema_fingerprint": schema_fingerprint,
    }


def _new_candidate(
    observation: GcObjectObservation,
    *,
    bucket: str,
    scan_id: str,
    schema_fingerprint: str,
    scanned_at: datetime,
    status: GcCandidateStatus,
    skip_reason: GcSkipReason | None,
) -> GcCandidate:
    clean = status is GcCandidateStatus.OBSERVED
    return GcCandidate(
        tenant_id=observation.tenant_id,
        status=status.value,
        bucket=bucket,
        key=observation.key,
        key_hash=_key_hash(observation.key),
        first_seen_at=scanned_at,
        last_clean_scan_at=scanned_at if clean else None,
        clean_scan_count=1 if clean else 0,
        scan_id=scan_id,
        schema_fingerprint=schema_fingerprint,
        evidence=_evidence(
            observation,
            scan_id=scan_id,
            schema_fingerprint=schema_fingerprint,
            scanned_at=scanned_at,
        ),
        object_version_id=observation.version_id,
        object_etag=observation.etag,
        object_size=observation.size,
        object_last_modified=observation.last_modified,
        skip_reason=skip_reason.value if skip_reason is not None else None,
        created_at=scanned_at,
        updated_at=scanned_at,
    )


def _same_object(candidate: GcCandidate, observation: GcObjectObservation) -> bool:
    return (
        candidate.object_version_id == observation.version_id
        and candidate.object_etag == observation.etag
        and candidate.object_size == observation.size
        and _as_utc(candidate.object_last_modified) == _as_utc(observation.last_modified)
    )


def _update_candidate_snapshot(
    candidate: GcCandidate,
    observation: GcObjectObservation,
    *,
    scan_id: str,
    schema_fingerprint: str,
    scanned_at: datetime,
) -> None:
    candidate.scan_id = scan_id
    candidate.schema_fingerprint = schema_fingerprint
    candidate.evidence = _evidence(
        observation,
        scan_id=scan_id,
        schema_fingerprint=schema_fingerprint,
        scanned_at=scanned_at,
    )
    candidate.object_version_id = observation.version_id
    candidate.object_etag = observation.etag
    candidate.object_size = observation.size
    candidate.object_last_modified = observation.last_modified
    candidate.updated_at = scanned_at


def _reset_skipped(
    candidate: GcCandidate,
    observation: GcObjectObservation,
    *,
    reason: GcSkipReason,
    scan_id: str,
    schema_fingerprint: str,
    scanned_at: datetime,
) -> None:
    candidate.status = GcCandidateStatus.SKIPPED.value
    candidate.skip_reason = reason.value
    candidate.first_seen_at = scanned_at
    candidate.last_clean_scan_at = None
    candidate.clean_scan_count = 0
    _update_candidate_snapshot(
        candidate,
        observation,
        scan_id=scan_id,
        schema_fingerprint=schema_fingerprint,
        scanned_at=scanned_at,
    )


def _restart_clean_observation(
    candidate: GcCandidate,
    observation: GcObjectObservation,
    *,
    scan_id: str,
    schema_fingerprint: str,
    scanned_at: datetime,
) -> None:
    candidate.status = GcCandidateStatus.OBSERVED.value
    candidate.skip_reason = None
    candidate.first_seen_at = scanned_at
    candidate.last_clean_scan_at = scanned_at
    candidate.clean_scan_count = 1
    _update_candidate_snapshot(
        candidate,
        observation,
        scan_id=scan_id,
        schema_fingerprint=schema_fingerprint,
        scanned_at=scanned_at,
    )


def _advance_clean_observation(
    candidate: GcCandidate,
    observation: GcObjectObservation,
    *,
    scan_id: str,
    schema_fingerprint: str,
    scanned_at: datetime,
) -> None:
    last_clean = (
        _as_utc(candidate.last_clean_scan_at)
        if candidate.last_clean_scan_at is not None
        else None
    )
    same_scan = candidate.scan_id == scan_id
    if not same_scan and (
        last_clean is None or scanned_at - last_clean >= MIN_CLEAN_SCAN_INTERVAL
    ):
        candidate.clean_scan_count += 1
        candidate.last_clean_scan_at = scanned_at
    _update_candidate_snapshot(
        candidate,
        observation,
        scan_id=scan_id,
        schema_fingerprint=schema_fingerprint,
        scanned_at=scanned_at,
    )
    first_seen = _as_utc(candidate.first_seen_at)
    if (
        candidate.clean_scan_count >= 2
        and scanned_at - first_seen >= MIN_FIRST_SEEN_AGE
    ):
        candidate.status = GcCandidateStatus.ELIGIBLE.value
        candidate.skip_reason = None


def observe_gc_candidates(
    db: Session,
    *,
    bucket: str,
    scan_id: str,
    schema_fingerprint: str,
    scanned_at: datetime,
    observations: Iterable[GcObjectObservation],
) -> GcObservationResult:
    scan_time = _as_utc(scanned_at)
    known_tenant_ids = set(db.scalars(select(Tenant.id)))
    touched: list[GcCandidate] = []
    skipped_without_candidate = 0

    for observation in sorted(observations, key=lambda item: item.key):
        classification = classify_storage_key(
            observation.key,
            known_tenant_ids=known_tenant_ids,
            expected_tenant_id=observation.tenant_id,
        )
        if classification.scope is not StorageKeyScope.TENANT:
            skipped_without_candidate += 1
            continue

        candidate = db.scalar(
            select(GcCandidate)
            .where(GcCandidate.bucket == bucket, GcCandidate.key == observation.key)
            .with_for_update()
        )
        if observation.referenced:
            if candidate is not None:
                _reset_skipped(
                    candidate,
                    observation,
                    reason=GcSkipReason.REFERENCE_FOUND,
                    scan_id=scan_id,
                    schema_fingerprint=schema_fingerprint,
                    scanned_at=scan_time,
                )
                touched.append(candidate)
            continue

        if candidate is None:
            identity_available = bool(observation.version_id)
            candidate = _new_candidate(
                observation,
                bucket=bucket,
                scan_id=scan_id,
                schema_fingerprint=schema_fingerprint,
                scanned_at=scan_time,
                status=(
                    GcCandidateStatus.OBSERVED
                    if identity_available
                    else GcCandidateStatus.SKIPPED
                ),
                skip_reason=(
                    None
                    if identity_available
                    else GcSkipReason.OBJECT_IDENTITY_UNAVAILABLE
                ),
            )
            db.add(candidate)
            touched.append(candidate)
            continue

        if not observation.version_id:
            _reset_skipped(
                candidate,
                observation,
                reason=GcSkipReason.OBJECT_IDENTITY_UNAVAILABLE,
                scan_id=scan_id,
                schema_fingerprint=schema_fingerprint,
                scanned_at=scan_time,
            )
        elif candidate.schema_fingerprint != schema_fingerprint:
            _reset_skipped(
                candidate,
                observation,
                reason=GcSkipReason.REFERENCE_COVERAGE_CHANGED,
                scan_id=scan_id,
                schema_fingerprint=schema_fingerprint,
                scanned_at=scan_time,
            )
        elif not _same_object(candidate, observation):
            _reset_skipped(
                candidate,
                observation,
                reason=GcSkipReason.OBJECT_CHANGED,
                scan_id=scan_id,
                schema_fingerprint=schema_fingerprint,
                scanned_at=scan_time,
            )
        elif candidate.status == GcCandidateStatus.SKIPPED.value:
            _restart_clean_observation(
                candidate,
                observation,
                scan_id=scan_id,
                schema_fingerprint=schema_fingerprint,
                scanned_at=scan_time,
            )
        elif candidate.status in {
            GcCandidateStatus.OBSERVED.value,
            GcCandidateStatus.ELIGIBLE.value,
        }:
            _advance_clean_observation(
                candidate,
                observation,
                scan_id=scan_id,
                schema_fingerprint=schema_fingerprint,
                scanned_at=scan_time,
            )
        touched.append(candidate)

    db.flush()
    return GcObservationResult(
        observed_count=sum(
            candidate.status == GcCandidateStatus.OBSERVED.value for candidate in touched
        ),
        eligible_count=sum(
            candidate.status == GcCandidateStatus.ELIGIBLE.value for candidate in touched
        ),
        skipped_count=skipped_without_candidate
        + sum(candidate.status == GcCandidateStatus.SKIPPED.value for candidate in touched),
    )
