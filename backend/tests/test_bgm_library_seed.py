from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_object_storage
from app.db.models import BgmLibraryTrack
from app.main import app
from app.services import bgm_library

EXPECTED_TRACKS = {
    "mixkit-gimme-that-groove-872": ("律动放克(欢快)", 88),
    "mixkit-cant-get-you-off-my-mind-1210": ("电子律动", 91),
    "mixkit-beautiful-dream-493": ("美梦(温暖)", 97),
    "mixkit-romantic-01-752": ("浪漫爵士", 99),
    "mixkit-cbpd-400": ("潮流陷阱", 99),
    "mixkit-complicated-281": ("都市说唱", 109),
    "mixkit-a-happy-child-532": ("温暖民谣", 112),
    "mixkit-serene-view-443": ("静谧轻松(口播)", 114),
    "mixkit-hip-hop-02-738": ("嘻哈节奏", 115),
    "mixkit-pop-05-695": ("慵懒流行", 154),
}


class _Storage:
    bucket = "bgm-test-bucket"

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.put_calls: list[tuple[str, str]] = []

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        self.objects[key] = (content, content_type)
        self.put_calls.append((key, content_type))
        return f"memory://{key}"

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key][0]

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        suffix = "&download=1" if download_filename else ""
        return f"https://cdn.test/{key}?ttl={expires_in}{suffix}"

    def delete_object(self, key: str) -> None:
        self.objects.pop(key, None)


def _assert_mixkit_tracks(items: list[dict[str, Any]]) -> None:
    assert len(items) == 10
    by_id = {item["track_id"]: item for item in items}
    assert set(by_id) == set(EXPECTED_TRACKS)
    for track_id, (name, duration_sec) in EXPECTED_TRACKS.items():
        item = by_id[track_id]
        assert item["name"] == name
        assert item["duration_sec"] == duration_sec
        assert item["license"] == "Mixkit License"
        assert f"platform/bgm/{track_id}.mp3" in item["preview_url"]


def test_seed_bgm_library_uploads_ten_mixkit_tracks_idempotently(auth_context, auth_db) -> None:
    storage = _Storage()

    with auth_db() as db:
        bgm_library.ensure_default_bgm_tracks(db, storage=storage)
        db.commit()
        bgm_library.ensure_default_bgm_tracks(db, storage=storage)
        db.commit()

        tracks = list(db.scalars(select(BgmLibraryTrack).order_by(BgmLibraryTrack.track_id)))

    assert len(tracks) == 10
    assert len(storage.objects) == 10
    assert len(storage.put_calls) == 10
    for track in tracks:
        expected_name, expected_duration = EXPECTED_TRACKS[track.track_id]
        assert track.name == expected_name
        assert track.duration_sec == expected_duration
        assert track.storage_key == f"platform/bgm/{track.track_id}.mp3"
        assert track.preview_storage_key == track.storage_key
        assert track.license == "Mixkit License"
        assert track.is_active is True
        content, content_type = storage.objects[track.storage_key]
        assert content_type == "audio/mpeg"
        assert content == (
            Path("seed_assets") / "bgm" / f"{track.track_id}.mp3"
        ).read_bytes()

    empty_storage = _Storage()
    with auth_db() as db:
        bgm_library.ensure_default_bgm_tracks(db, storage=empty_storage)
        db.commit()
        tracks_after_repair = list(db.scalars(select(BgmLibraryTrack)))

    assert len(tracks_after_repair) == 10
    assert len(empty_storage.objects) == 10
    assert len(empty_storage.put_calls) == 10


def test_bgm_library_endpoint_returns_ten_mixkit_tracks(auth_context, auth_db) -> None:
    storage = _Storage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        response = TestClient(app).get(
            "/api/v1/bgm-library",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    _assert_mixkit_tracks(items)
    assert len(storage.objects) == 10
