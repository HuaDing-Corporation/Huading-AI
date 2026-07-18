from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import GcCandidate, ReversePromptJob, VideoTask
from app.services.storage.base import StorageObjectIdentity
from scripts.ops.scan_orphan_objects import record_gc_candidates, scan_orphan_objects


class _Paginator:
    def __init__(self, *, key: str, modified_at: datetime) -> None:
        self.key = key
        self.modified_at = modified_at

    def paginate(self, **kwargs):
        return [
            {
                "Contents": [
                    {"Key": self.key, "Size": 12, "LastModified": self.modified_at}
                ]
            }
        ]


class _Client:
    def __init__(self, *, key: str, modified_at: datetime) -> None:
        self.paginator = _Paginator(key=key, modified_at=modified_at)

    def get_paginator(self, operation_name: str):
        return self.paginator


class _VersionedStorage:
    bucket = "media"

    def __init__(self, *, key: str, modified_at: datetime) -> None:
        self.key = key
        self.modified_at = modified_at
        self.client = _Client(key=key, modified_at=modified_at)

    def head_object_identity(self, key: str) -> StorageObjectIdentity:
        assert key == self.key
        return StorageObjectIdentity("version-one", "etag-one", 12, self.modified_at)


def _scan_and_record(db, *, storage, at: datetime, scan_id: str):
    report = scan_orphan_objects(db, storage=storage, now=at, scan_id=scan_id)
    record_gc_candidates(db, report)
    db.commit()
    return report


def test_soft_deleted_media_stays_protected_until_reference_clear_then_waits_seven_days(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    deleted_at = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    first_clean = deleted_at + timedelta(days=6)
    key = f"tenants/{tenant_id}/reverse-prompt/source.mp4"
    storage = _VersionedStorage(key=key, modified_at=deleted_at - timedelta(days=2))

    with auth_db() as db:
        job = ReversePromptJob(
            id="soft-deleted-reverse-job",
            tenant_id=tenant_id,
            source_kind="video",
            source_storage_key=key,
            target_format="seedance_2_0",
            status="succeeded",
            deleted_at=deleted_at,
        )
        db.add(job)
        db.commit()

        report = _scan_and_record(
            db,
            storage=storage,
            at=first_clean,
            scan_id="soft-delete-reference-present",
        )
        assert report.referenced_object_count == 1
        assert db.scalar(select(GcCandidate)) is None

        job.source_storage_key = None
        db.commit()
        _scan_and_record(
            db,
            storage=storage,
            at=first_clean,
            scan_id="soft-delete-first-clean",
        )
        candidate = db.scalar(select(GcCandidate))
        assert candidate.status == "observed"

        _scan_and_record(
            db,
            storage=storage,
            at=deleted_at + timedelta(days=7),
            scan_id="soft-delete-retention-boundary",
        )
        candidate = db.scalar(select(GcCandidate))
        assert candidate.status == "observed"

        _scan_and_record(
            db,
            storage=storage,
            at=first_clean + timedelta(days=7),
            scan_id="soft-delete-observation-complete",
        )
        candidate = db.scalar(select(GcCandidate))

    assert candidate.status == "eligible"


def test_hard_deleted_video_history_is_not_treated_as_a_soft_deleted_reference(
    auth_context,
    auth_db,
) -> None:
    tenant_id = auth_context["tenant_id"]
    scan_time = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    key = f"tenants/{tenant_id}/videos/hard-deleted.mp4"
    storage = _VersionedStorage(key=key, modified_at=scan_time - timedelta(days=2))

    with auth_db() as db:
        video = VideoTask(
            id="hard-deleted-video",
            tenant_id=tenant_id,
            mode="avatar_talk",
            video_mode="avatar_talk",
            status="done",
            storage_key=key,
        )
        db.add(video)
        db.commit()
        before = scan_orphan_objects(db, storage=storage, now=scan_time)
        assert before.referenced_object_count == 1

        db.delete(video)
        db.commit()
        after = _scan_and_record(
            db,
            storage=storage,
            at=scan_time,
            scan_id="hard-delete-first-clean",
        )
        candidate = db.scalar(select(GcCandidate))

    assert after.referenced_object_count == 0
    assert candidate.status == "observed"
    assert candidate.clean_scan_count == 1
