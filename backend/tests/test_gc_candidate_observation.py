from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import GcCandidate, Tenant
from app.services.gc.observation import GcObjectObservation, observe_gc_candidates

_FIRST_SCAN = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)


def _clean_observation(tenant_id: str) -> GcObjectObservation:
    return GcObjectObservation(
        tenant_id=tenant_id,
        key=f"tenants/{tenant_id}/uploads/orphan.bin",
        size=7,
        last_modified=_FIRST_SCAN - timedelta(days=2),
        version_id="version-one",
        etag="etag-one",
        referenced=False,
        reference_evidence={"matched_surfaces": [], "checked_surfaces": ["surface-a"]},
    )


def _observe(db, *, tenant_id: str, at: datetime, scan_id: str) -> GcCandidate:
    observe_gc_candidates(
        db,
        bucket="media",
        scan_id=scan_id,
        schema_fingerprint="f" * 64,
        scanned_at=at,
        observations=[_clean_observation(tenant_id)],
    )
    db.commit()
    return db.scalar(select(GcCandidate))


def test_candidate_requires_seven_days_and_clean_scans_twenty_four_hours_apart(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]

    with auth_db() as db:
        candidate = _observe(db, tenant_id=tenant_id, at=_FIRST_SCAN, scan_id="scan-one")
        assert candidate.status == "observed"
        assert candidate.clean_scan_count == 1

        candidate = _observe(
            db,
            tenant_id=tenant_id,
            at=_FIRST_SCAN + timedelta(hours=23, minutes=59),
            scan_id="scan-too-soon",
        )
        assert candidate.status == "observed"
        assert candidate.clean_scan_count == 1
        assert candidate.last_clean_scan_at == _FIRST_SCAN.replace(tzinfo=None)

        candidate = _observe(
            db,
            tenant_id=tenant_id,
            at=_FIRST_SCAN + timedelta(hours=24),
            scan_id="scan-two",
        )
        assert candidate.status == "observed"
        assert candidate.clean_scan_count == 2

        candidate = _observe(
            db,
            tenant_id=tenant_id,
            at=_FIRST_SCAN + timedelta(days=7),
            scan_id="scan-three",
        )
    assert candidate.status == "eligible"
    assert candidate.clean_scan_count == 3


def test_replayed_scan_id_cannot_count_as_a_second_clean_scan(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]

    with auth_db() as db:
        _observe(db, tenant_id=tenant_id, at=_FIRST_SCAN, scan_id="same-scan")
        candidate = _observe(
            db,
            tenant_id=tenant_id,
            at=_FIRST_SCAN + timedelta(days=7),
            scan_id="same-scan",
        )

    assert candidate.status == "observed"
    assert candidate.clean_scan_count == 1


def test_candidate_without_version_identity_never_becomes_eligible(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    observation = replace(_clean_observation(tenant_id), version_id=None)

    with auth_db() as db:
        for index, at in enumerate(
            (_FIRST_SCAN, _FIRST_SCAN + timedelta(days=7), _FIRST_SCAN + timedelta(days=14))
        ):
            observe_gc_candidates(
                db,
                bucket="local",
                scan_id=f"identity-scan-{index}",
                schema_fingerprint="f" * 64,
                scanned_at=at,
                observations=[observation],
            )
            db.commit()
        candidate = db.scalar(select(GcCandidate))

    assert candidate.status == "skipped"
    assert candidate.skip_reason == "object_identity_unavailable"
    assert candidate.clean_scan_count == 0


def test_reference_appearing_between_scans_resets_clean_observation(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    clean = _clean_observation(tenant_id)

    with auth_db() as db:
        _observe(db, tenant_id=tenant_id, at=_FIRST_SCAN, scan_id="clean-one")
        observe_gc_candidates(
            db,
            bucket="media",
            scan_id="reference-appeared",
            schema_fingerprint="f" * 64,
            scanned_at=_FIRST_SCAN + timedelta(days=7),
            observations=[replace(clean, referenced=True)],
        )
        db.commit()
        candidate = db.scalar(select(GcCandidate))
        assert candidate.status == "skipped"
        assert candidate.skip_reason == "reference_found"
        assert candidate.clean_scan_count == 0

        candidate = _observe(
            db,
            tenant_id=tenant_id,
            at=_FIRST_SCAN + timedelta(days=8),
            scan_id="clean-after-reference",
        )

    assert candidate.status == "observed"
    assert candidate.clean_scan_count == 1


def test_changed_object_version_restarts_observation_window(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    changed = replace(_clean_observation(tenant_id), version_id="version-two")

    with auth_db() as db:
        _observe(db, tenant_id=tenant_id, at=_FIRST_SCAN, scan_id="version-one-scan")
        observe_gc_candidates(
            db,
            bucket="media",
            scan_id="changed-version-scan",
            schema_fingerprint="f" * 64,
            scanned_at=_FIRST_SCAN + timedelta(days=7),
            observations=[changed],
        )
        db.commit()
        candidate = db.scalar(select(GcCandidate))
        assert candidate.status == "skipped"
        assert candidate.skip_reason == "object_changed"

        observe_gc_candidates(
            db,
            bucket="media",
            scan_id="changed-version-clean",
            schema_fingerprint="f" * 64,
            scanned_at=_FIRST_SCAN + timedelta(days=8),
            observations=[changed],
        )
        db.commit()
        candidate = db.scalar(select(GcCandidate))

    assert candidate.status == "observed"
    assert candidate.first_seen_at == (_FIRST_SCAN + timedelta(days=8)).replace(tzinfo=None)
    assert candidate.clean_scan_count == 1


@pytest.mark.parametrize(
    "unsafe_key",
    [
        "platform/avatars/catalog.png",
        "tenants/tenant-a/../tenant-b/nope.png",
        "unknown/prefix/nope.png",
    ],
)
def test_invalid_or_catalog_observation_never_creates_candidate(
    auth_context,
    auth_db,
    unsafe_key: str,
) -> None:
    tenant_id = auth_context["tenant_id"]
    observation = replace(_clean_observation(tenant_id), key=unsafe_key)

    with auth_db() as db:
        result = observe_gc_candidates(
            db,
            bucket="media",
            scan_id="unsafe-key-scan",
            schema_fingerprint="f" * 64,
            scanned_at=_FIRST_SCAN,
            observations=[observation],
        )
        db.commit()
        candidate_count = db.scalar(select(func.count()).select_from(GcCandidate))

    assert result.skipped_count == 1
    assert candidate_count == 0


def test_cross_tenant_observation_never_creates_candidate(auth_context, auth_db) -> None:
    tenant_id = auth_context["tenant_id"]
    other_tenant_id = "other-tenant"
    observation = replace(
        _clean_observation(tenant_id),
        key=f"tenants/{other_tenant_id}/uploads/cross.png",
    )

    with auth_db() as db:
        db.add(Tenant(id=other_tenant_id, slug="other-tenant", name="Other Tenant"))
        db.commit()
        result = observe_gc_candidates(
            db,
            bucket="media",
            scan_id="cross-tenant-scan",
            schema_fingerprint="f" * 64,
            scanned_at=_FIRST_SCAN,
            observations=[observation],
        )
        db.commit()
        candidate_count = db.scalar(select(func.count()).select_from(GcCandidate))

    assert result.skipped_count == 1
    assert candidate_count == 0
