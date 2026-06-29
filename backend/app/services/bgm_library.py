from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BgmLibraryTrack

_DEFAULT_TRACKS = [
    {
        "track_id": "ambient-soft-loop",
        "name": "Ambient Soft Loop",
        "duration_sec": 30,
        "storage_key": "library/bgm/ambient-soft-loop.mp3",
        "preview_storage_key": "library/bgm/ambient-soft-loop.mp3",
        "license": "Royalty-free CC0 placeholder; replace media before production.",
    },
    {
        "track_id": "bright-product-pop",
        "name": "Bright Product Pop",
        "duration_sec": 30,
        "storage_key": "library/bgm/bright-product-pop.mp3",
        "preview_storage_key": "library/bgm/bright-product-pop.mp3",
        "license": "Royalty-free CC0 placeholder; replace media before production.",
    },
    {
        "track_id": "calm-tech-pulse",
        "name": "Calm Tech Pulse",
        "duration_sec": 30,
        "storage_key": "library/bgm/calm-tech-pulse.mp3",
        "preview_storage_key": "library/bgm/calm-tech-pulse.mp3",
        "license": "Royalty-free CC0 placeholder; replace media before production.",
    },
]


def ensure_default_bgm_tracks(db: Session) -> None:
    existing = set(db.scalars(select(BgmLibraryTrack.track_id)))
    missing = [track for track in _DEFAULT_TRACKS if track["track_id"] not in existing]
    if not missing:
        return
    now = datetime.now(UTC)
    for track in missing:
        db.add(BgmLibraryTrack(**track, is_active=True, created_at=now))
    db.flush()


def list_bgm_tracks(db: Session) -> list[BgmLibraryTrack]:
    ensure_default_bgm_tracks(db)
    return list(
        db.scalars(
            select(BgmLibraryTrack)
            .where(BgmLibraryTrack.is_active.is_(True))
            .order_by(BgmLibraryTrack.track_id)
        )
    )
