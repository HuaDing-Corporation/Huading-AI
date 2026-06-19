from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Asset,
    Base,
    Subscription,
    TaskAsset,
    Tenant,
    UsageRecord,
    User,
    VideoTask,
    Voice,
)
from app.workers import avatar_talk


class _Store:
    def update(self, *args, **kwargs):
        pass


class _Storage:
    bucket = "bucket"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        self.objects[key] = content
        return f"memory://{key}"

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def presign_get_url(self, key: str, *, expires_in: int, download_filename: str | None = None):
        return f"https://storage.test/{key}"


def _session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False), engine


def test_default_avatar_talk_steps_create_assets_and_final_video(monkeypatch, tmp_path: Path):
    SessionTesting, engine = _session()
    tenant_id = "tenant-pipeline"
    task_id = "task-pipeline"
    storage = _Storage()
    storage.objects[f"tenants/{tenant_id}/uploads/avatar.png"] = b"PNG"
    now = datetime.now(UTC)

    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="pipe", name="Pipeline"))
        db.add(User(id="user-pipe", tenant_id=tenant_id, email="u@example.com", password_hash="h"))
        voice = Voice(
            id="voice-pipe",
            provider="edge-tts",
            voice_code="zh-CN-XiaoxiaoNeural",
            display_name="Xiaoxiao",
            gender="female",
        )
        avatar = Asset(
            id="avatar-asset",
            tenant_id=tenant_id,
            type="avatar_image",
            source="upload",
            storage_key=f"tenants/{tenant_id}/uploads/avatar.png",
            status="ready",
        )
        task = VideoTask(
            id=task_id,
            tenant_id=tenant_id,
            created_by_user_id="user-pipe",
            mode="avatar_talk",
            video_mode="avatar_talk",
            status="queued",
            progress=0,
            topic="topic",
            script="script",
            voice_id=voice.id,
        )
        sub = Subscription(
            id="sub-pipe",
            tenant_id=tenant_id,
            plan_id="plan-pipe",
            status="active",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=30),
            quota_credits_total=100,
            quota_credits_reserved=12,
        )
        usage = UsageRecord(
            tenant_id=tenant_id,
            subscription_id=sub.id,
            video_task_id=task_id,
            capability="avatar",
            provider="omnihuman",
            model="m",
            unit="second",
            quantity=Decimal("10"),
            credits=Decimal("12.00"),
            cost_cents=0,
            status="reserved",
        )
        db.add_all([voice, avatar, task, sub, usage])
        db.add(TaskAsset(video_task_id=task_id, asset_id=avatar.id, role="input_avatar"))
        db.commit()

    class _FakeTTS:
        def __init__(self, *, output_dir: str) -> None:
            self.output_dir = Path(output_dir)

        async def synthesize_speech(self, payload: dict):
            audio = self.output_dir / "audio.mp3"
            audio.parent.mkdir(parents=True, exist_ok=True)
            audio.write_bytes(b"MP3")
            return {
                "audio_path": str(audio),
                "timeline": [{"text": "script", "start_ms": 0, "end_ms": 1000}],
                "duration_ms": 1000,
                "mime_type": "audio/mpeg",
                "size_bytes": 3,
            }

    class _FakeOmni:
        async def generate_avatar(self, payload: dict):
            assert payload["image_url"].startswith("https://storage.test/")
            assert payload["audio_url"].startswith("https://storage.test/")
            return {"task_id": "cv-1", "video_url": "https://visual.test/video.mp4"}

    def fake_resolve(_db, *, tenant_id: str, capability: str):
        assert tenant_id == "tenant-pipeline"
        if capability == "tts":
            return _FakeTTS(output_dir=str(tmp_path))
        if capability == "avatar":
            return _FakeOmni()
        raise AssertionError(f"unexpected capability {capability}")

    monkeypatch.setattr(avatar_talk, "SessionLocal", SessionTesting)
    monkeypatch.setattr(avatar_talk, "build_progress_store", lambda _url: _Store())
    monkeypatch.setattr(avatar_talk, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(avatar_talk, "resolve", fake_resolve)
    monkeypatch.setattr(avatar_talk, "_download_bytes", lambda _url: b"BASE-MP4")
    monkeypatch.setattr(
        avatar_talk,
        "_burn_subtitles",
        lambda _base, _srt, output, target_size=(1080, 1920): output.write_bytes(
            b"COMPOSED-MP4"
        ),
    )
    monkeypatch.setattr(avatar_talk, "_work_dir", lambda _task_id: tmp_path)

    try:
        result = avatar_talk.run_avatar_talk_pipeline(tenant_id=tenant_id, task_id=task_id)

        assert result["status"] == "done"
        final_key = f"tenants/{tenant_id}/videos/{task_id}/final.mp4"
        assert storage.objects[final_key] == b"COMPOSED-MP4"
        with SessionTesting() as db:
            task = db.get(VideoTask, task_id)
            roles = {row.role for row in db.query(TaskAsset).filter_by(video_task_id=task_id)}
            assert task.storage_key == final_key
            assert {"input_avatar", "output_audio", "output_subtitle", "output_video"} <= roles
    finally:
        Base.metadata.drop_all(engine)


def test_script_step_generates_missing_script_with_deepseek(monkeypatch):
    SessionTesting, engine = _session()
    tenant_id = "tenant-script"
    task_id = "task-script"
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="script", name="Script"))
        db.add(
            VideoTask(
                id=task_id,
                tenant_id=tenant_id,
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="cashmere coat",
                script=None,
            )
        )
        db.commit()

        class _FakeDeepSeek:
            async def generate_text(self, payload: dict):
                assert payload["topic"] == "cashmere coat"
                return {"text": "Generated worker script"}

        def fake_resolve(db_arg, *, tenant_id: str, capability: str):
            assert db_arg is db
            assert tenant_id == "tenant-script"
            assert capability == "llm"
            return _FakeDeepSeek()

        monkeypatch.setattr(avatar_talk.settings, "engine_llm_api_key", "k")
        monkeypatch.setattr(avatar_talk.settings, "engine_llm_base_url", "https://deepseek.test")
        monkeypatch.setattr(avatar_talk.settings, "engine_llm_model", "m")
        monkeypatch.setattr(avatar_talk, "resolve", fake_resolve)
        ctx = avatar_talk.AvatarTalkContext(
            task_id=task_id,
            tenant_id=tenant_id,
            db=db,
            store=_Store(),
            storage=_Storage(),
        )

        avatar_talk.script_step(ctx)
        db.flush()

        assert db.get(VideoTask, task_id).script == "Generated worker script"

    Base.metadata.drop_all(engine)


def test_script_step_resolves_llm_provider_from_registry(monkeypatch):
    SessionTesting, engine = _session()
    tenant_id = "tenant-script-registry"
    task_id = "task-script-registry"
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="script-registry", name="Script Registry"))
        db.add(
            VideoTask(
                id=task_id,
                tenant_id=tenant_id,
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="wool coat",
                script=None,
            )
        )
        db.commit()

        class _RegistryDeepSeek:
            async def generate_text(self, payload: dict):
                assert payload["topic"] == "wool coat"
                return {"text": "Registry generated script"}

        def fake_resolve(db_arg, *, tenant_id: str, capability: str):
            assert db_arg is db
            assert tenant_id == "tenant-script-registry"
            assert capability == "llm"
            return _RegistryDeepSeek()

        def fail_direct_provider(**kwargs):
            raise AssertionError("script_step must resolve the LLM provider via registry")

        monkeypatch.setattr(avatar_talk.settings, "engine_llm_api_key", "k")
        monkeypatch.setattr(avatar_talk.settings, "engine_llm_base_url", "https://deepseek.test")
        monkeypatch.setattr(avatar_talk.settings, "engine_llm_model", "m")
        monkeypatch.setattr(avatar_talk, "resolve", fake_resolve, raising=False)
        monkeypatch.setattr(avatar_talk, "DeepSeekProvider", fail_direct_provider, raising=False)

        avatar_talk.script_step(
            avatar_talk.AvatarTalkContext(
                task_id=task_id,
                tenant_id=tenant_id,
                db=db,
                store=_Store(),
                storage=_Storage(),
            )
        )

        assert db.get(VideoTask, task_id).script == "Registry generated script"

    Base.metadata.drop_all(engine)


def test_subtitle_step_falls_back_to_script_when_timeline_is_empty(tmp_path: Path):
    SessionTesting, engine = _session()
    tenant_id = "tenant-subtitle-fallback"
    task_id = "subtitle-fallback-task"
    storage = _Storage()
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="subtitle-fallback", name="Subtitle Fallback"))
        db.add(
            VideoTask(
                id=task_id,
                tenant_id=tenant_id,
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="fallback topic",
                script="First sentence. Second sentence. Third sentence.",
            )
        )
        db.commit()
        ctx = avatar_talk.AvatarTalkContext(
            task_id=task_id,
            tenant_id=tenant_id,
            db=db,
            store=_Store(),
            storage=storage,
            duration_sec=6.0,
        )
        ctx.timeline = []

        avatar_talk.subtitle_step(ctx)

        subtitle_key = f"tenants/{tenant_id}/videos/{task_id}/subtitle.srt"
        srt = storage.objects[subtitle_key].decode("utf-8")
        srt_path = tmp_path / "subtitle.srt"
        srt_path.write_text(srt, encoding="utf-8")
        captions = avatar_talk._parse_srt(srt_path)
        assert [caption[2] for caption in captions] == [
            "First sentence.",
            "Second sentence.",
            "Third sentence.",
        ]
        assert captions[0][0] == 0
        assert captions[-1][1] == 6.0
        assert all(end > start for start, end, _text in captions)

    Base.metadata.drop_all(engine)


def test_subtitle_step_falls_back_when_timeline_has_no_effective_text(tmp_path: Path):
    SessionTesting, engine = _session()
    tenant_id = "tenant-subtitle-blank"
    task_id = "task-subtitle-blank"
    storage = _Storage()
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="subtitle-blank", name="Subtitle Blank"))
        db.add(
            VideoTask(
                id=task_id,
                tenant_id=tenant_id,
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="topic fallback",
                script="Visible fallback caption.",
            )
        )
        db.commit()
        ctx = avatar_talk.AvatarTalkContext(
            task_id=task_id,
            tenant_id=tenant_id,
            db=db,
            store=_Store(),
            storage=storage,
            duration_sec=3.0,
        )
        ctx.timeline = [{"text": "  ", "start_ms": 0, "end_ms": 3000}]

        avatar_talk.subtitle_step(ctx)

        subtitle_key = f"tenants/{tenant_id}/videos/{task_id}/subtitle.srt"
        srt = storage.objects[subtitle_key].decode("utf-8")
        srt_path = tmp_path / "subtitle.srt"
        srt_path.write_text(srt, encoding="utf-8")
        captions = avatar_talk._parse_srt(srt_path)
        assert captions == [(0.0, 3.0, "Visible fallback caption.")]

    Base.metadata.drop_all(engine)


def test_subtitle_step_preserves_word_boundary_timeline(tmp_path: Path):
    SessionTesting, engine = _session()
    tenant_id = "tenant-subtitle-timeline"
    task_id = "subtitle-timeline-task"
    storage = _Storage()
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="subtitle-timeline", name="Subtitle Timeline"))
        db.add(
            VideoTask(
                id=task_id,
                tenant_id=tenant_id,
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="timeline topic",
                script="Fallback must not be used.",
            )
        )
        db.commit()
        ctx = avatar_talk.AvatarTalkContext(
            task_id=task_id,
            tenant_id=tenant_id,
            db=db,
            store=_Store(),
            storage=storage,
            duration_sec=4.0,
        )
        ctx.timeline = [
            {"text": "real first", "start_ms": 500, "end_ms": 1500},
            {"text": "real second", "start_ms": 2000, "end_ms": 3200},
        ]

        avatar_talk.subtitle_step(ctx)

        subtitle_key = f"tenants/{tenant_id}/videos/{task_id}/subtitle.srt"
        srt = storage.objects[subtitle_key].decode("utf-8")
        srt_path = tmp_path / "subtitle.srt"
        srt_path.write_text(srt, encoding="utf-8")
        captions = avatar_talk._parse_srt(srt_path)
        assert captions == [(0.5, 1.5, "real first"), (2.0, 3.2, "real second")]

    Base.metadata.drop_all(engine)


def test_tts_step_uses_audio_file_duration_when_timeline_is_empty(monkeypatch, tmp_path: Path):
    SessionTesting, engine = _session()
    tenant_id = "tenant-audio-duration"
    unit_id = "subtitle-duration-job"
    storage = _Storage()
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="audio-duration", name="Audio Duration"))
        voice = Voice(
            id="voice-duration",
            provider="edge-tts",
            voice_code="zh-CN-XiaoxiaoNeural",
            display_name="Xiaoxiao",
            gender="female",
        )
        db.add(voice)
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="duration topic",
                script="duration script",
                voice_id=voice.id,
            )
        )
        db.commit()

        class _TTSNoTimeline:
            async def synthesize_speech(self, payload: dict):
                audio = tmp_path / "audio-duration.mp3"
                audio.write_bytes(b"MP3")
                return {
                    "audio_path": str(audio),
                    "timeline": [],
                    "duration_ms": 0,
                    "mime_type": "audio/mpeg",
                    "size_bytes": 3,
                }

        monkeypatch.setattr(
            avatar_talk,
            "resolve",
            lambda _db, *, tenant_id, capability: _TTSNoTimeline(),
        )
        monkeypatch.setattr(avatar_talk, "_audio_duration_sec", lambda _path: 9.8, raising=False)
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=tenant_id,
            db=db,
            store=_Store(),
            storage=storage,
        )

        avatar_talk.tts_step(ctx)

        assert ctx.duration_sec == 9.8
        assert storage.objects[f"tenants/{tenant_id}/videos/{unit_id}/audio.mp3"] == b"MP3"

    Base.metadata.drop_all(engine)


def test_subtitle_step_falls_back_when_timeline_covers_too_little_duration(tmp_path: Path):
    SessionTesting, engine = _session()
    tenant_id = "tenant-subtitle-partial"
    unit_id = "subtitle-partial-job"
    storage = _Storage()
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="subtitle-partial", name="Subtitle Partial"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="partial topic",
                script="First long sentence. Second long sentence. Third long sentence.",
            )
        )
        db.commit()
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=tenant_id,
            db=db,
            store=_Store(),
            storage=storage,
            duration_sec=10.0,
        )
        ctx.timeline = [{"text": "partial", "start_ms": 0, "end_ms": 1000}]

        avatar_talk.subtitle_step(ctx)

        subtitle_key = f"tenants/{tenant_id}/videos/{unit_id}/subtitle.srt"
        srt_path = tmp_path / "subtitle.srt"
        srt_path.write_text(storage.objects[subtitle_key].decode("utf-8"), encoding="utf-8")
        captions = avatar_talk._parse_srt(srt_path)
        assert [caption[2] for caption in captions] == [
            "First long sentence.",
            "Second long sentence.",
            "Third long sentence.",
        ]
        assert captions[-1][1] == 10.0

    Base.metadata.drop_all(engine)


def test_wrap_text_breaks_long_chinese_without_spaces():
    from PIL import Image, ImageDraw

    font = avatar_talk._font(24)
    image = Image.new("RGB", (220, 120))
    draw = ImageDraw.Draw(image)

    lines = avatar_talk._wrap_text(
        "这是一段没有空格的中文长句用于测试字幕换行不会溢出画面",
        max_width=120,
        draw=draw,
        font=font,
    )

    assert len(lines) > 1
    assert all(draw.textbbox((0, 0), line, font=font)[2] <= 120 for line in lines)


def test_subtitle_font_candidates_include_docker_cjk_font():
    assert "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc" in (
        avatar_talk._font_candidates()
    )


def test_subtitle_font_path_can_render_chinese_glyph(monkeypatch):
    candidates = [
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    font_path = next((path for path in candidates if path.exists()), None)
    if font_path is None:
        pytest.skip("No local CJK font available outside the backend Docker image.")
    monkeypatch.setattr(avatar_talk.settings, "engine_subtitle_font_path", str(font_path))

    font = avatar_talk._font(24)

    assert font.getmask("中").getbbox() is not None


def test_download_bytes_rejects_non_whitelisted_result_url(monkeypatch):
    called = False

    def fake_get(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("non-whitelisted URL must be rejected before requests.get")

    monkeypatch.setattr("requests.get", fake_get)

    with pytest.raises(RuntimeError, match="not allowed|whitelist"):
        avatar_talk._download_bytes("https://evil.example/result.mp4")

    assert called is False


def test_download_bytes_allows_configured_omnihuman_result_host_suffix(monkeypatch):
    class _Response:
        content = b"MP4"

        def raise_for_status(self) -> None:
            pass

    calls: list[dict] = []

    def fake_get(url: str, *, timeout: float):
        calls.append({"url": url, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(
        avatar_talk.settings,
        "engine_omnihuman_result_host_suffixes",
        "aigc-cloud.com",
    )
    monkeypatch.setattr("requests.get", fake_get)

    content = avatar_talk._download_bytes("https://v26-aiop.aigc-cloud.com/result.mp4")

    assert content == b"MP4"
    assert calls == [
        {
            "url": "https://v26-aiop.aigc-cloud.com/result.mp4",
            "timeout": avatar_talk.settings.engine_omnihuman_request_timeout_seconds,
        }
    ]


def test_download_bytes_rejects_evil_result_url_with_suffix_configured(monkeypatch):
    called = False

    def fake_get(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("non-whitelisted URL must be rejected before requests.get")

    monkeypatch.setattr(
        avatar_talk.settings,
        "engine_omnihuman_result_host_suffixes",
        "aigc-cloud.com",
    )
    monkeypatch.setattr("requests.get", fake_get)

    with pytest.raises(RuntimeError, match="not allowed|whitelist"):
        avatar_talk._download_bytes("https://aigc-cloud.com.evil.example/result.mp4")

    assert called is False


def test_download_bytes_rejects_http_result_url_with_suffix_configured(monkeypatch):
    called = False

    def fake_get(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("http URL must be rejected before requests.get")

    monkeypatch.setattr(
        avatar_talk.settings,
        "engine_omnihuman_result_host_suffixes",
        "aigc-cloud.com",
    )
    monkeypatch.setattr("requests.get", fake_get)

    with pytest.raises(RuntimeError, match="not allowed|whitelist"):
        avatar_talk._download_bytes("http://v26-aiop.aigc-cloud.com/result.mp4")

    assert called is False


def test_burn_subtitles_writes_9x16_video_with_visible_caption(tmp_path: Path):
    from moviepy.editor import ColorClip, VideoFileClip

    base = tmp_path / "base.mp4"
    srt = tmp_path / "caption.srt"
    output = tmp_path / "burned.mp4"
    ColorClip((90, 160), color=(0, 0, 0), duration=1).write_videofile(
        str(base),
        fps=5,
        codec="libx264",
        audio=False,
        logger=None,
    )
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nVISIBLE CAPTION\n",
        encoding="utf-8",
    )

    avatar_talk._burn_subtitles(base, srt, output, target_size=(180, 320))

    clip = VideoFileClip(str(output))
    try:
        assert clip.size == [180, 320]
        frame = clip.get_frame(0.5)
        bottom = frame[230:300, :, :]
        assert np.mean(bottom) > 5
    finally:
        clip.close()
