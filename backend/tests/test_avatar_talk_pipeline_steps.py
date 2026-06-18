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
