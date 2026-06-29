from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BgmLibraryTrack
from app.services.storage.base import ObjectStorage


@dataclass(frozen=True)
class BgmSeedTrack:
    filename: str
    name: str
    duration_sec: int
    license: str = "Mixkit License"

    @property
    def track_id(self) -> str:
        return Path(self.filename).stem

    @property
    def storage_key(self) -> str:
        return f"platform/bgm/{self.filename}"


BGM_SEED_ASSET_DIR = Path(__file__).resolve().parents[2] / "seed_assets" / "bgm"
BGM_SEED_TRACKS = [
    BgmSeedTrack("mixkit-gimme-that-groove-872.mp3", "律动放克(欢快)", 88),
    BgmSeedTrack("mixkit-cant-get-you-off-my-mind-1210.mp3", "电子律动", 91),
    BgmSeedTrack("mixkit-beautiful-dream-493.mp3", "美梦(温暖)", 97),
    BgmSeedTrack("mixkit-romantic-01-752.mp3", "浪漫爵士", 99),
    BgmSeedTrack("mixkit-cbpd-400.mp3", "潮流陷阱", 99),
    BgmSeedTrack("mixkit-complicated-281.mp3", "都市说唱", 109),
    BgmSeedTrack("mixkit-a-happy-child-532.mp3", "温暖民谣", 112),
    BgmSeedTrack("mixkit-serene-view-443.mp3", "静谧轻松(口播)", 114),
    BgmSeedTrack("mixkit-hip-hop-02-738.mp3", "嘻哈节奏", 115),
    BgmSeedTrack("mixkit-pop-05-695.mp3", "慵懒流行", 154),
]
_PLACEHOLDER_TRACK_IDS = {
    "ambient-soft-loop",
    "bright-product-pop",
    "calm-tech-pulse",
}


def _seed_asset_bytes(track: BgmSeedTrack) -> bytes:
    return (BGM_SEED_ASSET_DIR / track.filename).read_bytes()


def _storage_object_exists(storage: ObjectStorage, key: str) -> bool:
    object_exists = getattr(storage, "object_exists", None)
    if callable(object_exists):
        return bool(object_exists(key))
    try:
        storage.get_bytes(key)
    except Exception:
        return False
    return True


def _upload_seed_track(storage: ObjectStorage, track: BgmSeedTrack) -> None:
    if _storage_object_exists(storage, track.storage_key):
        return
    storage.put_bytes(
        track.storage_key,
        _seed_asset_bytes(track),
        content_type="audio/mpeg",
    )


def ensure_default_bgm_tracks(db: Session, *, storage: ObjectStorage | None = None) -> None:
    now = datetime.now(UTC)
    existing = {
        track.track_id: track for track in db.scalars(select(BgmLibraryTrack))
    }

    for placeholder_id in _PLACEHOLDER_TRACK_IDS:
        placeholder = existing.get(placeholder_id)
        if placeholder is not None:
            placeholder.is_active = False

    for seed in BGM_SEED_TRACKS:
        track = existing.get(seed.track_id)
        if storage is not None:
            _upload_seed_track(storage, seed)
        if track is None:
            db.add(
                BgmLibraryTrack(
                    track_id=seed.track_id,
                    name=seed.name,
                    duration_sec=seed.duration_sec,
                    storage_key=seed.storage_key,
                    preview_storage_key=seed.storage_key,
                    license=seed.license,
                    is_active=True,
                    created_at=now,
                )
            )
            continue
        track.name = seed.name
        track.duration_sec = seed.duration_sec
        track.storage_key = seed.storage_key
        track.preview_storage_key = seed.storage_key
        track.license = seed.license
        track.is_active = True
    db.flush()


def seed_bgm_library(
    db_factory: Callable[[], Session],
    *,
    storage: ObjectStorage | None = None,
) -> None:
    db = db_factory()
    try:
        ensure_default_bgm_tracks(db, storage=storage)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_bgm_tracks(db: Session) -> list[BgmLibraryTrack]:
    return list(
        db.scalars(
            select(BgmLibraryTrack)
            .where(BgmLibraryTrack.is_active.is_(True))
            .order_by(BgmLibraryTrack.track_id)
        )
    )
