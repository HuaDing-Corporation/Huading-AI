from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.api.deps import get_object_storage
from app.db.models import (
    Asset,
    CreditRate,
    Plan,
    Subscription,
    TaskAsset,
    Tenant,
    VideoTask,
    Voice,
)
from app.main import app
from app.schemas.videos import VideoGenerateRequest
from app.workers import avatar_talk


class _FakeStorage:
    def __init__(self) -> None:
        self.bucket = "cover-test-bucket"
        self.saved: dict[str, tuple[bytes, str]] = {}

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        self.saved[key] = (content, content_type)
        return f"memory://{key}"

    def get_bytes(self, key: str) -> bytes:
        return self.saved[key][0]

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        suffix = "&download=1" if download_filename else ""
        return f"https://storage.test/{key}?ttl={expires_in}{suffix}"


def _seed_billing(db, tenant_id: str) -> None:
    now = datetime.now(UTC)
    plan = Plan(
        code=f"oral-prod-plan-{tenant_id}",
        name="Oral Prod Plan",
        price_cents=0,
        period="monthly",
        quota_credits=500,
        max_concurrent=1,
        seat_limit=3,
    )
    db.add(plan)
    db.flush()
    db.add(
        Subscription(
            tenant_id=tenant_id,
            plan_id=plan.id,
            status="active",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=30),
            quota_credits_total=500,
            quota_credits_used=0,
            quota_credits_reserved=0,
        )
    )
    db.add_all(
        [
            CreditRate(
                tenant_id=None,
                capability="avatar",
                unit="second",
                credits_per_unit=Decimal("1.0000"),
            ),
            CreditRate(
                tenant_id=None,
                capability="tts",
                unit="second",
                credits_per_unit=Decimal("0.2000"),
            ),
            CreditRate(
                tenant_id=None,
                capability="image",
                unit="image",
                credits_per_unit=Decimal("5.0000"),
            ),
        ]
    )
    db.commit()


def _seed_voice_and_avatar(db, tenant_id: str) -> tuple[str, str]:
    voice = Voice(
        provider="edge-tts",
        voice_code="zh-CN-XiaoxiaoNeural",
        display_name="Xiaoxiao",
        gender="female",
        language="zh-CN",
        is_active=True,
    )
    avatar = Asset(
        tenant_id=tenant_id,
        type="avatar_image",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/avatar.png",
        mime_type="image/png",
        status="ready",
    )
    db.add_all([voice, avatar])
    db.commit()
    return voice.id, avatar.id


def _seed_completed_oral_task(
    db,
    *,
    tenant_id: str,
    user_id: str | None = None,
    task_id: str = "oral-cover-task",
    status: str = "done",
) -> VideoTask:
    task = VideoTask(
        id=task_id,
        tenant_id=tenant_id,
        created_by_user_id=user_id,
        mode="avatar_talk",
        video_mode="avatar_talk",
        status=status,
        progress=100 if status == "done" else 50,
        topic="oral cover source",
        script="oral script",
        storage_key=f"tenants/{tenant_id}/videos/{task_id}/final.mp4",
        duration_sec=10.0,
    )
    db.add(task)
    db.commit()
    return task


def test_subtitle_templates_endpoint_returns_frozen_presets(auth_context) -> None:
    client = TestClient(app)

    resp = client.get("/api/v1/oral/subtitle-templates", headers=auth_context["headers"])

    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    templates = body["data"]["templates"]
    assert [item["id"] for item in templates] == [
        "classic",
        "bold_yellow",
        "boxed",
        "minimal",
        "top_news",
    ]
    assert templates[0] == {
        "id": "classic",
        "name": "经典白",
        "font_family": "Noto Sans SC",
        "font_size": 48,
        "color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": 2,
        "background": None,
        "position": "bottom",
    }


def test_subtitle_style_validates_template_and_clamps_size() -> None:
    request = VideoGenerateRequest.model_validate(
        {
            "topic": "oral script",
            "voice_id": "voice-id",
            "avatar_asset_id": "avatar-id",
            "video_mode": "avatar_talk",
            "subtitle_style": {
                "template_id": "classic",
                "font_size": 999,
                "color": "#123ABC",
                "position": "center",
            },
        }
    )

    assert request.subtitle_style is not None
    assert request.subtitle_style.font_size == 96

    with pytest.raises(ValidationError):
        VideoGenerateRequest.model_validate(
            {
                "topic": "oral script",
                "voice_id": "voice-id",
                "avatar_asset_id": "avatar-id",
                "video_mode": "avatar_talk",
                "subtitle_style": {"template_id": "unknown"},
            }
        )


def test_avatar_order_persists_subtitle_style_only_when_provided(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        _seed_billing(db, auth_context["tenant_id"])
        voice_id, avatar_id = _seed_voice_and_avatar(db, auth_context["tenant_id"])

    enqueued: list[dict] = []

    class _FakeTask:
        def apply_async(self, *, args, task_id, queue=None):
            enqueued.append({"args": args, "task_id": task_id, "queue": queue})
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_avatar_talk_task", _FakeTask())
    client = TestClient(app)

    styled = client.post(
        "/api/v1/videos",
        json={
            "topic": "oral script",
            "voice_id": voice_id,
            "avatar_asset_id": avatar_id,
            "video_mode": "avatar_talk",
            "subtitle_style": {
                "template_id": "bold_yellow",
                "font_size": 4,
                "color": "#FFE600",
                "position": "top",
            },
        },
        headers=auth_context["headers"],
    )
    assert styled.status_code == 202
    styled_id = styled.json()["data"]["id"]

    plain = client.post(
        "/api/v1/videos",
        json={
            "topic": "oral script",
            "voice_id": voice_id,
            "avatar_asset_id": avatar_id,
            "video_mode": "avatar_talk",
        },
        headers=auth_context["headers"],
    )
    assert plain.status_code == 202
    plain_id = plain.json()["data"]["id"]

    with auth_db() as db:
        styled_task = db.get(VideoTask, styled_id)
        plain_task = db.get(VideoTask, plain_id)
    assert styled_task.params["subtitle_style"] == {
        "template_id": "bold_yellow",
        "font_size": 16,
        "color": "#FFE600",
        "position": "top",
    }
    assert "subtitle_style" not in plain_task.params
    assert enqueued[0]["args"][0]["subtitle_style"]["font_size"] == 16
    assert "subtitle_style" not in enqueued[1]["args"][0]


def test_compose_step_passes_resolved_subtitle_style(monkeypatch, auth_db, tmp_path) -> None:
    tenant_id = "tenant-style-compose"
    task_id = "style-compose-task"
    storage = _FakeStorage()
    subtitle_key = f"tenants/{tenant_id}/videos/{task_id}/subtitle.srt"
    storage.saved[subtitle_key] = (
        b"1\n00:00:00,000 --> 00:00:01,000\nCAPTION\n",
        "application/x-subrip",
    )
    with auth_db() as db:
        db.add(Tenant(id=tenant_id, slug="style-compose", name="Style Compose"))
        db.add(
            VideoTask(
                id=task_id,
                tenant_id=tenant_id,
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                progress=90,
                params={
                    "subtitle_style": {
                        "template_id": "top_news",
                        "font_size": 80,
                        "color": "#123456",
                        "position": "center",
                    }
                },
            )
        )
        db.commit()

        ctx = avatar_talk.AvatarTalkContext(
            task_id=task_id,
            tenant_id=tenant_id,
            db=db,
            store=None,
            storage=storage,
        )
        ctx.base_video_bytes = b"BASE"
        ctx.subtitle_key = subtitle_key
        calls: dict[str, object] = {}

        monkeypatch.setattr(avatar_talk, "_work_dir", lambda _task_id: tmp_path)

        def fake_burn_subtitles(_base, _subtitle, output, **kwargs):
            calls["kwargs"] = kwargs
            output.write_bytes(b"FINAL")

        monkeypatch.setattr(avatar_talk, "_burn_subtitles", fake_burn_subtitles)

        avatar_talk.compose_step(ctx)

    assert calls["kwargs"]["subtitle_style"] == {
        "id": "top_news",
        "name": "顶部条",
        "font_family": "Noto Sans SC",
        "font_size": 80,
        "color": "#123456",
        "stroke_color": "#000000",
        "stroke_width": 2,
        "background": "#0A0A0AB3",
        "position": "center",
    }


def test_default_subtitle_rendering_contract_stays_frozen(monkeypatch) -> None:
    from PIL import ImageDraw

    font_calls: list[dict[str, object]] = []
    text_calls: list[dict[str, object]] = []
    textbbox_calls: list[dict[str, object]] = []
    font_object = object()

    def fake_font(size: int, family: str | None = None):
        font_calls.append({"size": size, "family": family})
        return font_object

    class _RecordingDraw:
        def __init__(self, _image) -> None:
            pass

        def textbbox(self, xy, text, *, font):
            textbbox_calls.append({"xy": xy, "text": text, "font": font})
            return (0, 0, 144, 20)

        def text(self, xy, text, *, font, fill, **kwargs):
            text_calls.append(
                {
                    "xy": xy,
                    "text": text,
                    "font": font,
                    "fill": fill,
                    "kwargs": kwargs,
                }
            )

    monkeypatch.setattr(avatar_talk, "_font", fake_font)
    monkeypatch.setattr(ImageDraw, "Draw", _RecordingDraw)

    image = avatar_talk._subtitle_image("VISIBLE CAPTION", width=360, height=640)

    assert image.size == (360, 153)
    assert font_calls == [{"size": 28, "family": None}]
    assert textbbox_calls == [
        {"xy": (0, 0), "text": "VISIBLE", "font": font_object},
        {"xy": (0, 0), "text": "VISIBLE CAPTION", "font": font_object},
        {"xy": (0, 0), "text": "VISIBLE CAPTION", "font": font_object},
    ]
    assert text_calls == [
        {
            "xy": (106, 124),
            "text": "VISIBLE CAPTION",
            "font": font_object,
            "fill": (0, 0, 0, 230),
            "kwargs": {},
        },
        {
            "xy": (110, 124),
            "text": "VISIBLE CAPTION",
            "font": font_object,
            "fill": (0, 0, 0, 230),
            "kwargs": {},
        },
        {
            "xy": (108, 122),
            "text": "VISIBLE CAPTION",
            "font": font_object,
            "fill": (0, 0, 0, 230),
            "kwargs": {},
        },
        {
            "xy": (108, 126),
            "text": "VISIBLE CAPTION",
            "font": font_object,
            "fill": (0, 0, 0, 230),
            "kwargs": {},
        },
        {
            "xy": (108, 124),
            "text": "VISIBLE CAPTION",
            "font": font_object,
            "fill": (255, 255, 255, 255),
            "kwargs": {},
        },
    ]
    assert avatar_talk._caption_position(
        video_w=360,
        video_h=640,
        target_size=(360, 640),
        band_height=image.height,
    ) == ("center", 449)


def test_frame_candidates_clamp_count_and_store_previews(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services.covers import ExtractedFrame

    with auth_db() as db:
        _seed_completed_oral_task(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
        )
    storage = _FakeStorage()
    calls: dict[str, object] = {}

    def fake_extract_frame_candidates(storage_arg, *, video_key, timestamps):
        calls.update(
            {
                "storage": storage_arg,
                "video_key": video_key,
                "timestamps": timestamps,
            }
        )
        return [
            ExtractedFrame(timestamp_sec=timestamp, image_bytes=f"jpg-{index}".encode())
            for index, timestamp in enumerate(timestamps)
        ]

    from app.api.v1.routes import covers as covers_route

    monkeypatch.setattr(covers_route, "extract_frame_candidates", fake_extract_frame_candidates)
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/covers/frame-candidates?video_task_id=oral-cover-task&count=99",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 200
    frames = resp.json()["data"]["frames"]
    assert len(frames) == 10
    assert calls["timestamps"] == pytest.approx(
        [0.0, 1.1, 2.2, 3.3, 4.4, 5.5, 6.6, 7.7, 8.8, 9.9]
    )
    assert all(frame["preview_url"].startswith("https://storage.test/") for frame in frames)
    assert len(storage.saved) == 10


def test_cover_candidates_hide_cross_tenant_and_reject_unfinished(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        db.add(Tenant(id="other-tenant", slug="other", name="Other"))
        _seed_completed_oral_task(
            db,
            tenant_id="other-tenant",
            task_id="other-tenant-task",
        )
        _seed_completed_oral_task(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
            task_id="unfinished-task",
            status="running",
        )

    client = TestClient(app)
    hidden = client.get(
        "/api/v1/covers/frame-candidates?video_task_id=other-tenant-task",
        headers=auth_context["headers"],
    )
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "VIDEO_TASK_NOT_FOUND"

    unfinished = client.get(
        "/api/v1/covers/frame-candidates?video_task_id=unfinished-task",
        headers=auth_context["headers"],
    )
    assert unfinished.status_code == 409
    assert unfinished.json()["error"]["code"] == "VIDEO_TASK_NOT_READY"


def test_cover_from_frame_stores_cover_asset_and_updates_thumbnail(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services.covers import CoverImage

    with auth_db() as db:
        _seed_completed_oral_task(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
        )
    storage = _FakeStorage()
    calls: dict[str, object] = {}

    def fake_extract_frame_cover(storage_arg, *, video_key, timestamp_sec, title):
        calls.update(
            {
                "storage": storage_arg,
                "video_key": video_key,
                "timestamp_sec": timestamp_sec,
                "title": title,
            }
        )
        return CoverImage(image_bytes=b"cover-png", width=720, height=1280)

    from app.api.v1.routes import covers as covers_route

    monkeypatch.setattr(covers_route, "extract_frame_cover", fake_extract_frame_cover)
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/covers/from-frame",
            json={
                "video_task_id": "oral-cover-task",
                "timestamp_sec": 1.25,
                "title": {
                    "text": "Cover Title",
                    "font_size": 999,
                    "color": "#FFFFFF",
                    "position": "bottom",
                },
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 200
    cover = resp.json()["data"]["cover"]
    assert cover["image_url"].startswith("https://storage.test/")
    assert cover["width"] == 720
    assert cover["height"] == 1280
    assert calls["title"]["font_size"] == 120
    assert len(storage.saved) == 1
    storage_key = next(iter(storage.saved))
    assert storage.saved[storage_key] == (b"cover-png", "image/png")
    with auth_db() as db:
        asset = db.get(Asset, cover["id"])
        task = db.get(VideoTask, "oral-cover-task")
        link = db.scalars(select(TaskAsset).where(TaskAsset.asset_id == cover["id"])).one()
    assert asset.type == "generated_image"
    assert asset.metadata_["kind"] == "cover"
    assert asset.metadata_["purpose"] == "cover"
    assert asset.storage_key == storage_key
    assert task.thumbnail_key == storage_key
    assert link.role == "output_image"


def test_cover_from_frame_returns_friendly_timestamp_error(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services.covers import CoverTimestampOutOfRange

    with auth_db() as db:
        _seed_completed_oral_task(
            db,
            tenant_id=auth_context["tenant_id"],
            user_id=auth_context["user_id"],
        )

    def fake_extract_frame_cover(*_args, **_kwargs):
        raise CoverTimestampOutOfRange("timestamp_sec is outside the video duration.")

    from app.api.v1.routes import covers as covers_route

    monkeypatch.setattr(covers_route, "extract_frame_cover", fake_extract_frame_cover)
    app.dependency_overrides[get_object_storage] = lambda: _FakeStorage()
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/covers/from-frame",
            json={"video_task_id": "oral-cover-task", "timestamp_sec": 9999},
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "COVER_TIMESTAMP_OUT_OF_RANGE"


def test_photo_request_accepts_cover_purpose_for_ai_cover(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        _seed_billing(db, auth_context["tenant_id"])

    enqueued: dict[str, object] = {}

    class _FakeImageTask:
        def apply_async(self, *, args, task_id, queue=None):
            enqueued.update({"args": args, "task_id": task_id, "queue": queue})
            return type("Result", (), {"status": "PENDING"})()

    from app.api.v1.routes import videos as videos_route

    monkeypatch.setattr(videos_route, "generate_image_task", _FakeImageTask())
    client = TestClient(app)
    resp = client.post(
        "/api/v1/videos",
        json={
            "topic": "AI cover prompt",
            "video_mode": "photo",
            "purpose": "cover",
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 202
    task_id = resp.json()["data"]["id"]
    assert enqueued["args"][0]["purpose"] == "cover"
    with auth_db() as db:
        task = db.get(VideoTask, task_id)
    assert task.params["purpose"] == "cover"
