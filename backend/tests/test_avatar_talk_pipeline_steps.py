import json
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
    def __init__(self) -> None:
        self.events: list[tuple[tuple, dict]] = []

    def update(self, *args, **kwargs):
        self.events.append((args, kwargs))


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


BAD_SCRIPT_SAMPLE = (
    "【数字人主播脚本】\n"
    "（微笑，自然站姿，手持或展示裤子）\n"
    "# 标题\n"
    "> 口播脚本：**姐妹们，这条裤子显瘦又舒服，现在下单更划算。**"
)


def _max_same_value_stall_seconds(
    values: list[int],
    *,
    poll_interval_seconds: int,
) -> int:
    longest = 0
    stalled_for = 0
    previous = None
    for value in values:
        if previous is None or value > previous:
            stalled_for = 0
        elif value == previous:
            stalled_for += poll_interval_seconds
            longest = max(longest, stalled_for)
        else:
            raise AssertionError("progress must not go backwards")
        previous = value
    return longest


def _session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False), engine


def test_clean_spoken_script_removes_non_spoken_bad_sample():
    cleaned = avatar_talk.clean_spoken_script(BAD_SCRIPT_SAMPLE)

    assert cleaned == "姐妹们，这条裤子显瘦又舒服，现在下单更划算。"
    assert "【" not in cleaned
    assert "】" not in cleaned
    assert "（" not in cleaned
    assert "）" not in cleaned
    assert "数字人" not in cleaned
    assert "脚本" not in cleaned
    assert "*" not in cleaned
    assert "#" not in cleaned


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
        lambda _base, _srt, output, target_size=(1080, 1920), **_kwargs: output.write_bytes(
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


def test_seedance_i2v_step_generates_multiple_scenes_from_product_image(
    monkeypatch,
    tmp_path: Path,
):
    SessionTesting, engine = _session()
    tenant_id = "tenant-i2v-step"
    unit_id = "i2v-multi-job"
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="i2v-step", name="I2V Step"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                status="running",
                topic="soft scarf for winter gifting",
                script="soft scarf script",
                duration_sec=12,
                params={"image_key": "uploads/product.png", "duration_sec": 12},
            )
        )
        db.commit()

        image_path = tmp_path / "product.png"
        image_path.write_bytes(b"PNG")
        calls: list[dict[str, object]] = []

        def fake_generate(cfg, prompt, **kwargs):
            calls.append({"cfg": cfg, "prompt": prompt, "kwargs": kwargs})
            kwargs["progress_callback"]({"poll_count": 4})
            Path(kwargs["save_path"]).write_bytes(f"SCENE-{len(calls)}".encode())

        concat_calls: dict[str, object] = {}

        def fake_concat(scene_paths, output_path):
            concat_calls["scene_paths"] = [Path(path).name for path in scene_paths]
            concat_calls["output_path"] = Path(output_path).name
            Path(output_path).write_bytes(b"CONCAT-SEEDANCE-MP4")

        monkeypatch.setattr(avatar_talk, "_resolve_i2v_image", lambda params: str(image_path))
        monkeypatch.setattr(avatar_talk, "_seedance_engine_config", lambda: object())
        monkeypatch.setattr(
            avatar_talk,
            "_plan_seedance_i2v_scenes",
            lambda ctx, scene_count, clip_duration: [
                f"visual prompt {index + 1}" for index in range(scene_count)
            ],
        )
        monkeypatch.setattr(avatar_talk, "generate_seedance_video", fake_generate)
        monkeypatch.setattr(avatar_talk, "concat_seedance_clips", fake_concat)

        store = _Store()
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=tenant_id,
            db=db,
            store=store,
            storage=_Storage(),
            duration_sec=12,
        )

        result = avatar_talk.seedance_i2v_step(ctx)

        assert result.base_video_bytes == b"CONCAT-SEEDANCE-MP4"
        assert result.use_tts_audio is True
        assert len(calls) == 3
        assert [call["prompt"] for call in calls] == [
            "visual prompt 1",
            "visual prompt 2",
            "visual prompt 3",
        ]
        assert concat_calls["scene_paths"] == [
            "seedance_scene_00.mp4",
            "seedance_scene_01.mp4",
            "seedance_scene_02.mp4",
        ]
        for call in calls:
            kwargs = call["kwargs"]
            assert kwargs["image_path"] == str(image_path)
            assert kwargs["image_role"] == "first_frame"
            assert kwargs["ratio"] == "9:16"
            assert kwargs["resolution"] == "720p"
            assert kwargs["generate_audio"] is False
            assert kwargs["duration"] == 5
        assert not any(call["kwargs"]["duration"] == 12 for call in calls)
        frame_events = [
            event[1].get("frame_current")
            for event in store.events
            if event[1].get("frame_total")
        ]
        assert frame_events == [
            1,
            2,
            3,
        ]

    Base.metadata.drop_all(engine)


def test_seedance_i2v_progress_advances_on_every_poll_without_stalling(
    monkeypatch,
    tmp_path: Path,
):
    SessionTesting, engine = _session()
    tenant_id = "tenant-i2v-progress"
    unit_id = "i2v-progress-job"
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="i2v-progress", name="I2V Progress"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                status="running",
                topic="soft scarf for winter gifting",
                script="soft scarf script",
                duration_sec=15,
                params={"image_key": "uploads/product.png", "duration_sec": 15},
            )
        )
        db.commit()

        image_path = tmp_path / "product.png"
        image_path.write_bytes(b"PNG")
        elapsed_seconds = 0
        poll_interval_seconds = 5

        def fake_generate(cfg, prompt, **kwargs):
            nonlocal elapsed_seconds
            for poll_count in range(1, 9):
                elapsed_seconds += poll_interval_seconds
                kwargs["progress_callback"](
                    {
                        "poll_count": poll_count,
                        "status": "running",
                        "step_elapsed_seconds": elapsed_seconds,
                    }
                )
            Path(kwargs["save_path"]).write_bytes(b"SCENE-MP4")

        def fake_concat(scene_paths, output_path):
            Path(output_path).write_bytes(b"CONCAT-SEEDANCE-MP4")

        monkeypatch.setattr(avatar_talk, "_resolve_i2v_image", lambda params: str(image_path))
        monkeypatch.setattr(avatar_talk, "_seedance_engine_config", lambda: object())
        monkeypatch.setattr(
            avatar_talk,
            "_plan_seedance_i2v_scenes",
            lambda ctx, scene_count, clip_duration: [
                f"visual prompt {index + 1}" for index in range(scene_count)
            ],
        )
        monkeypatch.setattr(avatar_talk, "generate_seedance_video", fake_generate)
        monkeypatch.setattr(avatar_talk, "concat_seedance_clips", fake_concat)

        store = _Store()
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=tenant_id,
            db=db,
            store=store,
            storage=_Storage(),
            duration_sec=15,
        )

        avatar_talk.seedance_i2v_step(ctx)

        seedance_events = [
            event[1]
            for event in store.events
            if event[1].get("stage") == "seedance_generating"
        ]
        progresses = [event["progress"] for event in seedance_events]
        frame_pairs = [
            (event["frame_current"], event["frame_total"]) for event in seedance_events
        ]

        assert len(progresses) == 24
        assert progresses == sorted(progresses)
        assert len(set(progresses)) > 1
        assert _max_same_value_stall_seconds(
            progresses,
            poll_interval_seconds=poll_interval_seconds,
        ) < 120
        assert min(progresses) >= 25
        assert max(progresses) <= 88
        assert frame_pairs == [(1, 3)] * 8 + [(2, 3)] * 8 + [(3, 3)] * 8

    Base.metadata.drop_all(engine)


def test_seedance_i2v_long_single_scene_polling_does_not_trip_watchdog(
    monkeypatch,
    tmp_path: Path,
):
    SessionTesting, engine = _session()
    tenant_id = "tenant-i2v-long-progress"
    unit_id = "i2v-long-progress-job"
    poll_interval_seconds = 5
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="i2v-long-progress", name="I2V Long Progress"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                status="running",
                topic="soft scarf for winter gifting",
                script="soft scarf script",
                duration_sec=15,
                params={"image_key": "uploads/product.png", "duration_sec": 15},
            )
        )
        db.commit()

        image_path = tmp_path / "product.png"
        image_path.write_bytes(b"PNG")
        elapsed_seconds = 0
        generate_calls = 0

        def fake_generate(cfg, prompt, **kwargs):
            nonlocal elapsed_seconds, generate_calls
            generate_calls += 1
            poll_total = 120 if generate_calls == 1 else 1
            for poll_count in range(1, poll_total + 1):
                elapsed_seconds += poll_interval_seconds
                kwargs["progress_callback"](
                    {
                        "poll_count": poll_count,
                        "status": "running",
                        "step_elapsed_seconds": elapsed_seconds,
                    }
                )
            Path(kwargs["save_path"]).write_bytes(b"SCENE-MP4")

        def fake_concat(scene_paths, output_path):
            Path(output_path).write_bytes(b"CONCAT-SEEDANCE-MP4")

        monkeypatch.setattr(avatar_talk, "_resolve_i2v_image", lambda params: str(image_path))
        monkeypatch.setattr(avatar_talk, "_seedance_engine_config", lambda: object())
        monkeypatch.setattr(
            avatar_talk,
            "_plan_seedance_i2v_scenes",
            lambda ctx, scene_count, clip_duration: [
                f"visual prompt {index + 1}" for index in range(scene_count)
            ],
        )
        monkeypatch.setattr(avatar_talk, "generate_seedance_video", fake_generate)
        monkeypatch.setattr(avatar_talk, "concat_seedance_clips", fake_concat)

        store = _Store()
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=tenant_id,
            db=db,
            store=store,
            storage=_Storage(),
            duration_sec=15,
        )

        avatar_talk.seedance_i2v_step(ctx)

        progresses = [
            event[1]["progress"]
            for event in store.events
            if event[1].get("stage") == "seedance_generating"
        ]

        assert len(progresses) == 122
        assert _max_same_value_stall_seconds(
            progresses,
            poll_interval_seconds=poll_interval_seconds,
        ) < 120
        assert progresses == sorted(progresses)
        assert max(progresses) < 88

    Base.metadata.drop_all(engine)


def test_seedance_i2v_24_scene_progress_keeps_watchdog_alive(
    monkeypatch,
    tmp_path: Path,
):
    SessionTesting, engine = _session()
    tenant_id = "tenant-i2v-max-progress"
    unit_id = "i2v-max-progress-job"
    poll_interval_seconds = 5
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="i2v-max-progress", name="I2V Max Progress"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                status="running",
                topic="soft scarf for winter gifting",
                script="soft scarf script",
                duration_sec=120,
                params={"image_key": "uploads/product.png", "duration_sec": 120},
            )
        )
        db.commit()

        image_path = tmp_path / "product.png"
        image_path.write_bytes(b"PNG")
        elapsed_seconds = 0

        def fake_generate(cfg, prompt, **kwargs):
            nonlocal elapsed_seconds
            for poll_count in range(1, 121):
                elapsed_seconds += poll_interval_seconds
                kwargs["progress_callback"](
                    {
                        "poll_count": poll_count,
                        "status": "running",
                        "step_elapsed_seconds": elapsed_seconds,
                    }
                )
            Path(kwargs["save_path"]).write_bytes(b"SCENE-MP4")

        def fake_concat(scene_paths, output_path):
            Path(output_path).write_bytes(b"CONCAT-SEEDANCE-MP4")

        monkeypatch.setattr(avatar_talk, "_resolve_i2v_image", lambda params: str(image_path))
        monkeypatch.setattr(avatar_talk, "_seedance_engine_config", lambda: object())
        monkeypatch.setattr(
            avatar_talk,
            "_plan_seedance_i2v_scenes",
            lambda ctx, scene_count, clip_duration: [
                f"visual prompt {index + 1}" for index in range(scene_count)
            ],
        )
        monkeypatch.setattr(avatar_talk, "generate_seedance_video", fake_generate)
        monkeypatch.setattr(avatar_talk, "concat_seedance_clips", fake_concat)

        store = _Store()
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=tenant_id,
            db=db,
            store=store,
            storage=_Storage(),
            duration_sec=120,
        )

        avatar_talk.seedance_i2v_step(ctx)

        progresses = [
            event[1]["progress"]
            for event in store.events
            if event[1].get("stage") == "seedance_generating"
        ]

        assert len(progresses) == 24 * 120
        assert _max_same_value_stall_seconds(
            progresses,
            poll_interval_seconds=poll_interval_seconds,
        ) < 120
        assert progresses == sorted(progresses)
        assert max(progresses) < 88

    Base.metadata.drop_all(engine)


def test_seedance_i2v_scene_planner_requests_visual_prompts(monkeypatch):
    SessionTesting, engine = _session()
    tenant_id = "tenant-i2v-scenes"
    unit_id = "i2v-scenes-job"
    payloads: list[dict] = []
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="i2v-scenes", name="I2V Scenes"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                status="running",
                topic="水墨陶瓷碗",
                script="这只水墨陶瓷碗很适合送礼，现在下单了解更多。",
                duration_sec=15,
                params={"duration_sec": 15},
            )
        )
        db.commit()

        class _FakeDeepSeek:
            async def generate_text(self, payload: dict):
                payloads.append(payload)
                return {
                    "text": json.dumps(
                        [
                            "镜头1：产品从水墨背景中缓慢推进，突出釉面质感。",
                            "镜头2：俯拍碗口与礼盒组合，强调送礼场景。",
                            "镜头3：侧逆光环绕展示器型，收束到行动号召氛围。",
                        ],
                        ensure_ascii=False,
                    )
                }

        monkeypatch.setattr(
            avatar_talk,
            "resolve",
            lambda _db, *, tenant_id, capability: _FakeDeepSeek(),
        )
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=tenant_id,
            db=db,
            store=_Store(),
            storage=_Storage(),
            duration_sec=15,
        )

        prompts = avatar_talk._plan_seedance_i2v_scenes(ctx, scene_count=3, clip_duration=5)

        payload = payloads[0]
        assert payload["video_mode"] == "seedance_i2v"
        assert payload["scene_count"] == 3
        assert payload["clip_duration_sec"] == 5
        assert payload["target_duration_sec"] == 15
        assert "视觉分镜" in payload["system_prompt"]
        assert "first_frame" in payload["user_prompt"]
        assert prompts == [
            "镜头1：产品从水墨背景中缓慢推进，突出釉面质感。",
            "镜头2：俯拍碗口与礼盒组合，强调送礼场景。",
            "镜头3：侧逆光环绕展示器型，收束到行动号召氛围。",
        ]

    Base.metadata.drop_all(engine)


def test_seedance_i2v_scene_planner_uses_scene_prompt_not_script(monkeypatch):
    SessionTesting, engine = _session()
    tenant_id = "tenant-i2v-visual"
    unit_id = "i2v-visual-unit"
    payloads: list[dict] = []
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="i2v-visual", name="I2V Visual"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                status="running",
                topic="premium ceramic mug",
                script="spoken copy that must never drive the visual plan",
                duration_sec=10,
                params={
                    "duration_sec": 10,
                    "scene_prompt": "sunlit tabletop product video with steam",
                },
            )
        )
        db.commit()

        class _FakeDeepSeek:
            async def generate_text(self, payload: dict):
                payloads.append(payload)
                return {"text": json.dumps(["visual scene one", "visual scene two"])}

        monkeypatch.setattr(
            avatar_talk,
            "resolve",
            lambda _db, *, tenant_id, capability: _FakeDeepSeek(),
        )
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=tenant_id,
            db=db,
            store=_Store(),
            storage=_Storage(),
            duration_sec=10,
        )

        prompts = avatar_talk._plan_seedance_i2v_scenes(ctx, scene_count=2, clip_duration=5)

        payload = payloads[0]
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        assert payload["scene_prompt"] == "sunlit tabletop product video with steam"
        assert payload["topic"] == "premium ceramic mug"
        assert "script" not in payload
        assert "spoken copy that must never drive the visual plan" not in serialized_payload
        assert prompts == ["visual scene one", "visual scene two"]

    Base.metadata.drop_all(engine)


def test_seedance_i2v_scene_planner_falls_back_to_topic_without_script_leak(monkeypatch):
    SessionTesting, engine = _session()
    tenant_id = "tenant-i2v-topic"
    unit_id = "i2v-topic-unit"
    payloads: list[dict] = []
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="i2v-topic", name="I2V Topic"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                status="running",
                topic="minimalist travel bottle",
                script="voiceover copy should stay out of fallback prompts",
                duration_sec=10,
                params={"duration_sec": 10, "scene_prompt": "  "},
            )
        )
        db.commit()

        class _FakeDeepSeek:
            async def generate_text(self, payload: dict):
                payloads.append(payload)
                return {"text": ""}

        monkeypatch.setattr(
            avatar_talk,
            "resolve",
            lambda _db, *, tenant_id, capability: _FakeDeepSeek(),
        )
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=tenant_id,
            db=db,
            store=_Store(),
            storage=_Storage(),
            duration_sec=10,
        )

        prompts = avatar_talk._plan_seedance_i2v_scenes(ctx, scene_count=2, clip_duration=5)

        serialized_payload = json.dumps(payloads[0], ensure_ascii=False)
        assert payloads[0]["scene_prompt"] == "minimalist travel bottle"
        assert "voiceover copy should stay out of fallback prompts" not in serialized_payload
        assert all("minimalist travel bottle" in prompt for prompt in prompts)
        assert all(
            "voiceover copy should stay out of fallback prompts" not in prompt
            for prompt in prompts
        )

    Base.metadata.drop_all(engine)


def test_seedance_i2v_runner_releases_quota_on_failure(monkeypatch):
    SessionTesting, engine = _session()
    tenant_id = "tenant-i2v-fail"
    unit_id = "i2v-fail-job"
    store = _Store()
    storage = _Storage()
    now = datetime.now(UTC)
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="i2v-fail", name="I2V Fail"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                status="queued",
                topic="product",
                params={"image_key": "uploads/product.png"},
            )
        )
        sub = Subscription(
            id="sub-i2v-fail",
            tenant_id=tenant_id,
            plan_id="plan-i2v-fail",
            status="active",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=30),
            quota_credits_total=100,
            quota_credits_reserved=10,
        )
        db.add(sub)
        db.add(
            UsageRecord(
                tenant_id=tenant_id,
                subscription_id=sub.id,
                video_task_id=unit_id,
                capability="video",
                provider="seedance",
                model="doubao-seedance-2-0-260128",
                unit="second",
                quantity=Decimal("5"),
                credits=Decimal("10.00"),
                cost_cents=0,
                status="reserved",
            )
        )
        db.commit()

    def fail_step(ctx):
        raise TimeoutError("Seedance timed out after 600s")

    monkeypatch.setattr(avatar_talk, "SessionLocal", SessionTesting)
    monkeypatch.setattr(avatar_talk, "build_progress_store", lambda _url: store)
    monkeypatch.setattr(avatar_talk, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(avatar_talk, "ECOM_I2V_STEPS", [("seedance", 80, fail_step)])

    try:
        with pytest.raises(TimeoutError, match="Seedance timed out"):
            avatar_talk.run_seedance_i2v_pipeline(tenant_id=tenant_id, task_id=unit_id)

        with SessionTesting() as db:
            task = db.get(VideoTask, unit_id)
            sub = db.get(Subscription, "sub-i2v-fail")
            usage = db.query(UsageRecord).filter_by(video_task_id=unit_id).one()
            assert task.status == "failed"
            assert task.error_code == "SEEDANCE_I2V_FAILED"
            assert "Seedance timed out" in task.error_message
            assert sub.quota_credits_reserved == 0
            assert usage.status == "released"
        assert store.events[-1][1]["status"] == "failed"
        assert store.events[-1][1]["error_code"] == "SEEDANCE_I2V_FAILED"
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


def test_script_step_keeps_avatar_prompt_payload_unchanged(monkeypatch):
    SessionTesting, engine = _session()
    tenant_id = "tenant-script-avatar"
    unit_id = "script-avatar-job"
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="script-avatar", name="Script Avatar"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="wool coat",
                script=None,
            )
        )
        db.commit()

        class _FakeDeepSeek:
            async def generate_text(self, payload: dict):
                assert payload == {"topic": "wool coat"}
                return {"text": "Avatar script"}

        monkeypatch.setattr(avatar_talk.settings, "engine_llm_api_key", "k")
        monkeypatch.setattr(avatar_talk.settings, "engine_llm_base_url", "https://deepseek.test")
        monkeypatch.setattr(avatar_talk.settings, "engine_llm_model", "m")
        monkeypatch.setattr(
            avatar_talk,
            "resolve",
            lambda _db, *, tenant_id, capability: _FakeDeepSeek(),
        )

        avatar_talk.script_step(
            avatar_talk.AvatarTalkContext(
                task_id=unit_id,
                tenant_id=tenant_id,
                db=db,
                store=_Store(),
                storage=_Storage(),
            )
        )

        assert db.get(VideoTask, unit_id).script == "Avatar script"

    Base.metadata.drop_all(engine)


def test_script_step_uses_ecommerce_prompt_and_duration_budget(monkeypatch):
    SessionTesting, engine = _session()
    tenant_id = "tenant-script-ecom"
    unit_id = "script-ecom-job"
    payloads: list[dict] = []
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="script-ecom", name="Script Ecom"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                status="running",
                topic="新中式水墨陶瓷碗，适合送礼，质感高级",
                script=None,
                duration_sec=30,
                params={"duration_sec": 30},
            )
        )
        db.commit()

        class _FakeDeepSeek:
            async def generate_text(self, payload: dict):
                payloads.append(payload)
                return {"text": "这只陶瓷碗质感温润，送礼自用都很有面子，现在下单了解更多。"}

        monkeypatch.setattr(avatar_talk.settings, "engine_llm_api_key", "k")
        monkeypatch.setattr(avatar_talk.settings, "engine_llm_base_url", "https://deepseek.test")
        monkeypatch.setattr(avatar_talk.settings, "engine_llm_model", "m")
        monkeypatch.setattr(
            avatar_talk,
            "resolve",
            lambda _db, *, tenant_id, capability: _FakeDeepSeek(),
        )

        avatar_talk.script_step(
            avatar_talk.AvatarTalkContext(
                task_id=unit_id,
                tenant_id=tenant_id,
                db=db,
                store=_Store(),
                storage=_Storage(),
            )
        )

        payload = payloads[0]
        assert payload["topic"] == "新中式水墨陶瓷碗，适合送礼，质感高级"
        assert payload["video_mode"] == "seedance_i2v"
        assert payload["target_duration_sec"] == 30
        assert payload["target_chars_min"] == 150
        assert payload["target_chars_max"] == 180
        assert "电商带货" in payload["system_prompt"]
        assert "卖点" in payload["user_prompt"]
        assert "行动号召" in payload["user_prompt"]
        assert "30秒" in payload["user_prompt"]
        assert db.get(VideoTask, unit_id).script.startswith("这只陶瓷碗")

    Base.metadata.drop_all(engine)


def test_script_step_cleans_seedance_i2v_copy_to_spoken_sales_text(monkeypatch):
    SessionTesting, engine = _session()
    tenant_id = "tenant-script-clean"
    unit_id = "script-clean-ecom"
    payloads: list[dict] = []
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="script-clean", name="Script Clean"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                status="running",
                topic="premium ceramic mug",
                script=None,
                duration_sec=15,
                params={"duration_sec": 15},
            )
        )
        db.commit()

        class _FakeDeepSeek:
            async def generate_text(self, payload: dict):
                payloads.append(payload)
                return {
                    "text": "# 1. 镜头推进，数字人微笑致意。**这款杯子保温耐用，办公送礼都合适。**"
                }

        monkeypatch.setattr(avatar_talk.settings, "engine_llm_api_key", "k")
        monkeypatch.setattr(avatar_talk.settings, "engine_llm_base_url", "https://deepseek.test")
        monkeypatch.setattr(avatar_talk.settings, "engine_llm_model", "m")
        monkeypatch.setattr(
            avatar_talk,
            "resolve",
            lambda _db, *, tenant_id, capability: _FakeDeepSeek(),
        )

        avatar_talk.script_step(
            avatar_talk.AvatarTalkContext(
                task_id=unit_id,
                tenant_id=tenant_id,
                db=db,
                store=_Store(),
                storage=_Storage(),
            )
        )

        stored = db.get(VideoTask, unit_id).script
        assert stored == "这款杯子保温耐用，办公送礼都合适。"
        assert all(token not in stored for token in ("#", "*", "镜头", "数字人"))
        assert "镜头/运镜/画面" in payloads[0]["user_prompt"]
        assert "Markdown" in payloads[0]["user_prompt"]

    Base.metadata.drop_all(engine)


def test_script_step_cleans_user_bad_sample_before_tts(monkeypatch):
    SessionTesting, engine = _session()
    tenant_id = "tenant-script-bad"
    unit_id = "script-bad-ecom"
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="script-bad", name="Script Bad"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                status="running",
                topic="pants product",
                script=None,
                duration_sec=10,
                params={"duration_sec": 10},
            )
        )
        db.commit()

        class _FakeDeepSeek:
            async def generate_text(self, payload: dict):
                return {"text": BAD_SCRIPT_SAMPLE}

        monkeypatch.setattr(avatar_talk.settings, "engine_llm_api_key", "k")
        monkeypatch.setattr(avatar_talk.settings, "engine_llm_base_url", "https://deepseek.test")
        monkeypatch.setattr(avatar_talk.settings, "engine_llm_model", "m")
        monkeypatch.setattr(
            avatar_talk,
            "resolve",
            lambda _db, *, tenant_id, capability: _FakeDeepSeek(),
        )

        avatar_talk.script_step(
            avatar_talk.AvatarTalkContext(
                task_id=unit_id,
                tenant_id=tenant_id,
                db=db,
                store=_Store(),
                storage=_Storage(),
            )
        )

        assert db.get(VideoTask, unit_id).script == "姐妹们，这条裤子显瘦又舒服，现在下单更划算。"

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


def test_subtitle_step_times_script_clauses_from_character_timeline(tmp_path: Path):
    SessionTesting, engine = _session()
    tenant_id = "tenant-subtitle-timeline"
    task_id = "subtitle-timeline-task"
    storage = _Storage()
    script = "real first. real second."
    timeline = _caption_timeline_from_script(script, step_ms=100)
    for item in timeline:
        item["start_ms"] = int(item["start_ms"]) + 500
        item["end_ms"] = int(item["end_ms"]) + 500
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
                script=script,
            )
        )
        db.commit()
        ctx = avatar_talk.AvatarTalkContext(
            task_id=task_id,
            tenant_id=tenant_id,
            db=db,
            store=_Store(),
            storage=storage,
            duration_sec=timeline[-1]["end_ms"] / 1000,
        )
        ctx.timeline = timeline

        avatar_talk.subtitle_step(ctx)

        subtitle_key = f"tenants/{tenant_id}/videos/{task_id}/subtitle.srt"
        srt = storage.objects[subtitle_key].decode("utf-8")
        srt_path = tmp_path / "subtitle.srt"
        srt_path.write_text(srt, encoding="utf-8")
        captions = avatar_talk._parse_srt(srt_path)
        assert captions == [(0.5, 1.4, "real first."), (1.4, 2.4, "real second.")]

    Base.metadata.drop_all(engine)


def _word_timeline(
    text: str,
    *,
    step_ms: int = 120,
    gap_after_index: int | None = None,
    gap_ms: int = 0,
) -> list[dict[str, int | str]]:
    timeline: list[dict[str, int | str]] = []
    cursor = 0
    for index, char in enumerate(text):
        start = cursor
        end = start + step_ms
        timeline.append({"text": char, "start_ms": start, "end_ms": end})
        cursor = end
        if gap_after_index is not None and index == gap_after_index:
            cursor += gap_ms
    return timeline


_CAPTION_PUNCTUATION = set(".!?;。！？；，、\n")


def _caption_timeline_from_script(script: str, *, step_ms: int = 100):
    return _word_timeline(
        "".join(
            char
            for char in script
            if char not in _CAPTION_PUNCTUATION and not char.isspace()
        ),
        step_ms=step_ms,
    )


def _timeline_from_spans(spans: list[tuple[int, int]]) -> list[dict[str, int | str]]:
    return [
        {"text": f"w{index}", "start_ms": start_ms, "end_ms": end_ms}
        for index, (start_ms, end_ms) in enumerate(spans)
    ]


def _subtitle_captions_for_script(
    tmp_path: Path,
    *,
    script: str,
    timeline: list[dict[str, int | str]],
    duration_sec: float,
):
    SessionTesting, engine = _session()
    tenant_id = "tenant-punct-seg"
    unit_id = "subtitle-punct-seg"
    storage = _Storage()
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="punct-seg", name="Punct Seg"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="punct topic",
                script=script,
            )
        )
        db.commit()
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=tenant_id,
            db=db,
            store=_Store(),
            storage=storage,
            duration_sec=duration_sec,
        )
        ctx.timeline = timeline

        avatar_talk.subtitle_step(ctx)

        subtitle_key = f"tenants/{tenant_id}/videos/{unit_id}/subtitle.srt"
        srt_path = tmp_path / f"{unit_id}.srt"
        srt_path.write_text(storage.objects[subtitle_key].decode("utf-8"), encoding="utf-8")
        captions = avatar_talk._parse_srt(srt_path)

    Base.metadata.drop_all(engine)
    return captions


def test_subtitle_step_does_not_stretch_captions_into_trailing_silence(tmp_path: Path):
    script = "aaaaaaaaaa;bbbbbbbbbb;cccccccccc;dddddddddd."
    timeline = _timeline_from_spans(
        [
            (
                round(225 + index * (9235 - 225) / 49),
                round(225 + (index + 1) * (9235 - 225) / 49),
            )
            for index in range(49)
        ]
    )

    captions = _subtitle_captions_for_script(
        tmp_path,
        script=script,
        timeline=timeline,
        duration_sec=10.76,
    )

    assert [caption[2] for caption in captions] == [
        "aaaaaaaaaa;",
        "bbbbbbbbbb;",
        "cccccccccc;",
        "dddddddddd.",
    ]
    assert captions[-1][1] == 9.235
    assert captions[-1][1] != 10.76
    assert all(caption[1] <= 9.235 for caption in captions)


def test_subtitle_step_uses_real_token_times_when_counts_differ(tmp_path: Path):
    script = "aa;bb."
    timeline = _timeline_from_spans(
        [
            (0, 100),
            (100, 200),
            (200, 900),
            (1700, 1800),
            (1800, 1900),
            (1900, 2000),
        ]
    )

    captions = _subtitle_captions_for_script(
        tmp_path,
        script=script,
        timeline=timeline,
        duration_sec=2.0,
    )

    assert captions == [(0.0, 0.9, "aa;"), (1.7, 2.0, "bb.")]


def test_subtitle_step_falls_back_when_timeline_has_fewer_items_than_clauses(
    tmp_path: Path,
):
    captions = _subtitle_captions_for_script(
        tmp_path,
        script="aa;bb.",
        timeline=_timeline_from_spans([(1000, 5000)]),
        duration_sec=5.0,
    )

    assert captions == [(0.0, 2.5, "aa;"), (2.5, 5.0, "bb.")]


def test_subtitle_step_uses_script_punctuation_for_clauses(tmp_path: Path):
    script = "用华鼎AI短视频引擎，一键生成专业级营销视频，让企业内容生产效率提升10倍，效果更精准。"
    timeline = _caption_timeline_from_script(script, step_ms=100)
    duration_sec = timeline[-1]["end_ms"] / 1000

    captions = _subtitle_captions_for_script(
        tmp_path,
        script=script,
        timeline=timeline,
        duration_sec=duration_sec,
    )

    assert [caption[2] for caption in captions] == [
        "用华鼎AI短视频引擎，",
        "一键生成专业级营销视频，",
        "让企业内容生产效率提升10倍，",
        "效果更精准。",
    ]
    assert captions[0][0] == timeline[0]["start_ms"] / 1000
    first_clause_len = len("用华鼎AI短视频引擎")
    assert captions[0][1] == timeline[first_clause_len - 1]["end_ms"] / 1000
    assert captions[1][0] == timeline[first_clause_len]["start_ms"] / 1000
    assert captions[-1][1] == timeline[-1]["end_ms"] / 1000


def test_subtitle_step_uses_indexed_token_times_when_timeline_length_differs(
    tmp_path: Path,
):
    script = "价格提升10倍，效果更精准。"
    timeline = _word_timeline("价格提升十倍效果更精准", step_ms=200)
    duration_sec = timeline[-1]["end_ms"] / 1000

    captions = _subtitle_captions_for_script(
        tmp_path,
        script=script,
        timeline=timeline,
        duration_sec=duration_sec,
    )

    assert [caption[2] for caption in captions] == ["价格提升10倍，", "效果更精准。"]
    assert captions[0][0] == timeline[0]["start_ms"] / 1000
    assert captions[-1][1] == timeline[-1]["end_ms"] / 1000
    assert captions[0][1] == captions[1][0]
    assert captions[0][1] == timeline[5]["end_ms"] / 1000


def test_subtitle_step_distributes_script_clauses_without_timeline(tmp_path: Path):
    captions = _subtitle_captions_for_script(
        tmp_path,
        script="短句，长一点。",
        timeline=[],
        duration_sec=5.0,
    )

    assert captions == [(0.0, 2.0, "短句，"), (2.0, 5.0, "长一点。")]


def test_subtitle_step_keeps_long_unpunctuated_script_as_single_cue(tmp_path: Path):
    script = "这是一个没有标点但非常长的字幕文本用于验证不会拆成多条字幕"
    timeline = _caption_timeline_from_script(script, step_ms=80)

    captions = _subtitle_captions_for_script(
        tmp_path,
        script=script,
        timeline=timeline,
        duration_sec=timeline[-1]["end_ms"] / 1000,
    )

    assert captions == [(0.0, timeline[-1]["end_ms"] / 1000, script)]


def test_caption_position_stays_inside_letterboxed_content():
    band_height = 260
    position = avatar_talk._caption_position(
        video_w=1080,
        video_h=1080,
        target_size=(1080, 1920),
        band_height=band_height,
    )

    assert position[0] == "center"
    y = position[1]
    content_top = 420
    content_bottom = 1500
    margin = 64
    assert content_top <= y
    assert y + band_height <= content_bottom - margin


def test_caption_position_falls_back_near_bottom_for_full_height_video():
    position = avatar_talk._caption_position(
        video_w=1080,
        video_h=1920,
        target_size=(1080, 1920),
        band_height=260,
    )

    assert position[0] == "center"
    assert 1200 <= position[1] <= 1660


def test_caption_word_segmentation_falls_back_when_jieba_fails(monkeypatch):
    def fail_lcut(_text: str) -> list[str]:
        raise RuntimeError("jieba unavailable")

    monkeypatch.setattr(avatar_talk, "_jieba_lcut", fail_lcut, raising=False)

    assert avatar_talk._segment_caption_words("零门槛") == ["零", "门", "槛"]


def test_subtitle_step_strips_markdown_from_fallback_script(tmp_path: Path):
    SessionTesting, engine = _session()
    tenant_id = "tenant-caption-clean"
    unit_id = "caption-clean-job"
    storage = _Storage()
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="caption-clean", name="Caption Clean"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="caption clean",
                script="# 标题：**价值** `让每个人` *看见*",
            )
        )
        db.commit()
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=tenant_id,
            db=db,
            store=_Store(),
            storage=storage,
            duration_sec=4.0,
        )
        ctx.timeline = []

        avatar_talk.subtitle_step(ctx)

        subtitle_key = f"tenants/{tenant_id}/videos/{unit_id}/subtitle.srt"
        srt_path = tmp_path / "subtitle.srt"
        srt_path.write_text(storage.objects[subtitle_key].decode("utf-8"), encoding="utf-8")
        captions = avatar_talk._parse_srt(srt_path)
        text = " ".join(caption[2] for caption in captions)
        assert "价值" in text
        assert "让每个人" in text
        assert "*" not in text
        assert "#" not in text
        assert "`" not in text

    Base.metadata.drop_all(engine)


def test_subtitle_step_strips_markdown_from_script_text(tmp_path: Path):
    SessionTesting, engine = _session()
    tenant_id = "tenant-caption-timeline"
    unit_id = "caption-timeline-job"
    storage = _Storage()
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="caption-timeline", name="Caption Timeline"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="caption timeline",
                script="**真实** `字幕`",
            )
        )
        db.commit()
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=tenant_id,
            db=db,
            store=_Store(),
            storage=storage,
            duration_sec=2.0,
        )
        ctx.timeline = _caption_timeline_from_script("真实 字幕", step_ms=500)

        avatar_talk.subtitle_step(ctx)

        subtitle_key = f"tenants/{tenant_id}/videos/{unit_id}/subtitle.srt"
        srt_path = tmp_path / "subtitle.srt"
        srt_path.write_text(storage.objects[subtitle_key].decode("utf-8"), encoding="utf-8")
        captions = avatar_talk._parse_srt(srt_path)
        assert captions == [(0.0, 2.0, "真实 字幕")]

    Base.metadata.drop_all(engine)


def test_subtitle_step_cleans_user_bad_sample_from_srt(tmp_path: Path):
    SessionTesting, engine = _session()
    tenant_id = "tenant-caption-bad"
    unit_id = "caption-bad-job"
    storage = _Storage()
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="caption-bad", name="Caption Bad"))
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="seedance_i2v",
                video_mode="seedance_i2v",
                status="running",
                topic="caption bad",
                script=BAD_SCRIPT_SAMPLE,
            )
        )
        db.commit()
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=tenant_id,
            db=db,
            store=_Store(),
            storage=storage,
            duration_sec=3.0,
        )
        ctx.timeline = []

        avatar_talk.subtitle_step(ctx)

        subtitle_key = f"tenants/{tenant_id}/videos/{unit_id}/subtitle.srt"
        srt_path = tmp_path / "subtitle.srt"
        srt_path.write_text(storage.objects[subtitle_key].decode("utf-8"), encoding="utf-8")
        captions = avatar_talk._parse_srt(srt_path)
        text = " ".join(caption[2] for caption in captions)
        assert text.replace(" ", "") == "姐妹们，这条裤子显瘦又舒服，现在下单更划算。"
        assert all(token not in text for token in ("【", "】", "（", "）", "#", "*", "脚本"))

    Base.metadata.drop_all(engine)


def test_avatar_step_publishes_progress_heartbeat_during_provider_polling():
    SessionTesting, engine = _session()
    tenant_id = "tenant-avatar-progress"
    unit_id = "avatar-progress-job"
    storage = _Storage()
    with SessionTesting() as db:
        db.add(Tenant(id=tenant_id, slug="avatar-progress", name="Avatar Progress"))
        avatar = Asset(
            id="avatar-progress-asset",
            tenant_id=tenant_id,
            type="avatar_image",
            source="upload",
            storage_key=f"tenants/{tenant_id}/uploads/avatar.png",
            status="ready",
        )
        db.add(avatar)
        db.add(
            VideoTask(
                id=unit_id,
                tenant_id=tenant_id,
                mode="avatar_talk",
                video_mode="avatar_talk",
                status="running",
                topic="avatar progress",
                script="avatar progress script",
            )
        )
        db.add(TaskAsset(video_task_id=unit_id, asset_id=avatar.id, role="input_avatar"))
        db.commit()

        class _AvatarProvider:
            async def generate_avatar(self, payload: dict):
                payload["progress_callback"]({"poll_count": 1, "status": "in_queue"})
                payload["progress_callback"]({"poll_count": 2, "status": "processing"})
                return {"video_url": "https://visual.example/result.mp4"}

        store = _Store()
        ctx = avatar_talk.AvatarTalkContext(
            task_id=unit_id,
            tenant_id=tenant_id,
            db=db,
            store=store,
            storage=storage,
        )
        ctx.audio_key = f"tenants/{tenant_id}/videos/{unit_id}/audio.mp3"

        original_resolve = avatar_talk.resolve
        original_download = avatar_talk._download_bytes
        try:
            avatar_talk.resolve = lambda _db, *, tenant_id, capability: _AvatarProvider()
            avatar_talk._download_bytes = lambda _url: b"MP4"
            avatar_talk.avatar_step(ctx)
        finally:
            avatar_talk.resolve = original_resolve
            avatar_talk._download_bytes = original_download

        progress_events = [kwargs for _args, kwargs in store.events]
        assert [event["progress"] for event in progress_events] == [25, 26]
        assert {event["step"] for event in progress_events} == {"avatar"}
        assert {event["stage"] for event in progress_events} == {"avatar_generating"}

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


def test_wrap_text_keeps_chinese_terms_on_the_same_line():
    from PIL import Image, ImageDraw

    font = avatar_talk._font(24)
    image = Image.new("RGB", (320, 120))
    draw = ImageDraw.Draw(image)
    text = "零门槛制作高质量营销视频"
    max_width = draw.textbbox((0, 0), "零门槛制作高", font=font)[2]

    lines = avatar_talk._wrap_text(text, max_width=max_width, draw=draw, font=font)

    assert "".join(lines) == text
    assert any("高质量" in line for line in lines)
    assert any("营销视频" in line for line in lines)
    assert all("高" not in line or "高质量" in line for line in lines)


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


def test_subtitle_image_keeps_transparent_background_without_black_box(monkeypatch):
    from PIL import ImageDraw

    calls = []

    def fake_rounded_rectangle(self, *args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(ImageDraw.ImageDraw, "rounded_rectangle", fake_rounded_rectangle)

    image = avatar_talk._subtitle_image("VISIBLE CAPTION", width=360, height=640)

    assert calls == []
    assert image.mode == "RGBA"


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


def test_compose_end_time_trims_only_long_trailing_silence():
    assert avatar_talk._compose_end_time(10.92, 9.235) == pytest.approx(9.735)
    assert avatar_talk._compose_end_time(10.0, 9.5) == pytest.approx(10.0)
    assert avatar_talk._compose_end_time(10.0, None) == pytest.approx(10.0)
    assert avatar_talk._compose_end_time(0.8, 0.2) == pytest.approx(0.8)
    assert avatar_talk._compose_end_time(10.0, 9.9) >= 9.9


def test_aigc_metadata_params_include_comment_and_json_label():
    params = avatar_talk._aigc_metadata_params(
        task_id="video-alpha",
        producer="Huading",
        propagate_id="tenant-alpha",
    )

    assert params[:2] == [
        "-metadata",
        "comment=本视频由AI生成合成（AIGC）。生成服务：Huading；内容编号：video-alpha",
    ]
    assert "description=AI-generated content (AIGC)" in params
    label_value = next(item for item in params if item.startswith("aigc_label="))
    label = json.loads(label_value.removeprefix("aigc_label="))
    assert label == {
        "is_ai_generated": True,
        "producer": "Huading",
        "content_id": "video-alpha",
        "propagate_id": "tenant-alpha",
    }


def test_compose_step_passes_aigc_metadata_context(monkeypatch, tmp_path: Path):
    storage = _Storage()
    subtitle_key = "tenants/tenant-alpha/videos/video-alpha/subtitle.srt"
    storage.objects[subtitle_key] = b"1\n00:00:00,000 --> 00:00:01,000\nCAPTION\n"
    ctx = avatar_talk.AvatarTalkContext(
        task_id="video-alpha",
        tenant_id="tenant-alpha",
        db=None,
        store=_Store(),
        storage=storage,
    )
    ctx.base_video_bytes = b"MP4"
    ctx.subtitle_key = subtitle_key
    calls: dict[str, object] = {}

    monkeypatch.setattr(avatar_talk.settings, "engine_aigc_producer", "Settings Producer")
    monkeypatch.setattr(avatar_talk, "_work_dir", lambda _task_id: tmp_path)

    def fake_burn_subtitles(base, subtitle, output, **kwargs):
        calls["base"] = base
        calls["subtitle"] = subtitle
        calls["output"] = output
        calls["kwargs"] = kwargs
        output.write_bytes(b"FINAL")

    monkeypatch.setattr(avatar_talk, "_burn_subtitles", fake_burn_subtitles)

    result = avatar_talk.compose_step(ctx)

    assert result.final_video_bytes == b"FINAL"
    assert calls["kwargs"] == {
        "task_id": "video-alpha",
        "producer": "Settings Producer",
        "propagate_id": "tenant-alpha",
    }


def test_burn_subtitles_trims_tail_and_applies_audio_fadeout(monkeypatch, tmp_path: Path):
    import moviepy.editor as moviepy_editor

    calls: dict[str, object] = {}

    class _Audio:
        def fx(self, effect, duration):
            calls["fadeout_duration"] = duration
            calls["fadeout_effect"] = getattr(effect, "__name__", str(effect))
            return self

    class _Video:
        w = 1080
        h = 1920
        duration = 10.92
        audio = _Audio()

        def __init__(self, _path=None):
            pass

        def resize(self, _scale):
            return self

        def set_position(self, _position):
            return self

        def close(self):
            calls["video_closed"] = True

    class _ColorClip:
        def __init__(self, _size, *, color, duration):
            self.color = color
            self.duration = duration

    class _ImageClip:
        def __init__(self, _array, *, transparent):
            self.transparent = transparent

        def set_start(self, start):
            self.start = start
            return self

        def set_duration(self, duration):
            self.duration = duration
            return self

        def set_position(self, position):
            self.position = position
            return self

    class _CompositeVideoClip:
        def __init__(self, clips, *, size):
            calls["clip_count"] = len(clips)
            calls["size"] = size
            self.audio = None
            self.duration = None

        def set_duration(self, duration):
            self.duration = duration
            return self

        def set_audio(self, audio):
            self.audio = audio
            calls["set_audio"] = True
            return self

        def subclip(self, start, end):
            calls["subclip"] = (start, end)
            self.duration = end - start
            return self

        def write_videofile(self, path, **kwargs):
            calls["write_path"] = path
            calls["write_kwargs"] = kwargs

        def close(self):
            calls["final_closed"] = True

    monkeypatch.setattr(moviepy_editor, "VideoFileClip", _Video)
    monkeypatch.setattr(moviepy_editor, "ColorClip", _ColorClip)
    monkeypatch.setattr(moviepy_editor, "ImageClip", _ImageClip)
    monkeypatch.setattr(moviepy_editor, "CompositeVideoClip", _CompositeVideoClip)

    base = tmp_path / "base.mp4"
    subtitle = tmp_path / "caption.srt"
    output = tmp_path / "burned.mp4"
    base.write_bytes(b"MP4")
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:09,235\nVISIBLE CAPTION\n",
        encoding="utf-8",
    )

    avatar_talk._burn_subtitles(
        base,
        subtitle,
        output,
        task_id="video-alpha",
        producer="Huading",
        propagate_id="tenant-alpha",
    )

    assert calls["subclip"] == (0, pytest.approx(9.735))
    assert calls["fadeout_duration"] == pytest.approx(0.3)
    assert calls["write_kwargs"]["codec"] == "libx264"
    assert calls["write_kwargs"]["audio_codec"] == "aac"
    assert calls["write_kwargs"]["audio_bitrate"] == "192k"
    assert calls["write_kwargs"]["audio_fps"] == 44100
    assert calls["write_kwargs"]["audio"] is True
    ffmpeg_params = calls["write_kwargs"]["ffmpeg_params"]
    assert ffmpeg_params[:2] == [
        "-metadata",
        "comment=本视频由AI生成合成（AIGC）。生成服务：Huading；内容编号：video-alpha",
    ]
    assert "description=AI-generated content (AIGC)" in ffmpeg_params
    label_value = next(item for item in ffmpeg_params if item.startswith("aigc_label="))
    label = json.loads(label_value.removeprefix("aigc_label="))
    assert label["is_ai_generated"] is True
    assert label["content_id"] == "video-alpha"
    assert label["propagate_id"] == "tenant-alpha"


def test_burn_subtitles_uses_external_tts_audio_and_freezes_video_tail(
    monkeypatch,
    tmp_path: Path,
):
    import moviepy.editor as moviepy_editor

    calls: dict[str, object] = {}

    class _ExternalAudio:
        duration = 3.0

        def fx(self, effect, duration):
            calls["fadeout_duration"] = duration
            return self

        def close(self):
            calls["audio_closed"] = True

    class _Video:
        w = 720
        h = 1280
        duration = 1.0
        audio = None

        def __init__(self, _path=None):
            pass

        def fx(self, effect, **kwargs):
            calls["freeze_effect"] = getattr(effect, "__name__", str(effect))
            calls["freeze_kwargs"] = kwargs
            self.duration += float(kwargs["freeze_duration"])
            return self

        def resize(self, _scale):
            return self

        def set_position(self, _position):
            return self

        def close(self):
            calls["video_closed"] = True

    class _ColorClip:
        def __init__(self, _size, *, color, duration):
            calls["canvas_duration"] = duration

    class _ImageClip:
        def __init__(self, _array, *, transparent):
            self.transparent = transparent

        def set_start(self, start):
            self.start = start
            return self

        def set_duration(self, duration):
            self.duration = duration
            return self

        def set_position(self, position):
            self.position = position
            return self

    class _CompositeVideoClip:
        def __init__(self, clips, *, size):
            self.audio = None
            self.duration = None

        def set_duration(self, duration):
            self.duration = duration
            calls["final_duration"] = duration
            return self

        def set_audio(self, audio):
            self.audio = audio
            calls["set_audio"] = audio
            return self

        def subclip(self, start, end):
            calls["subclip"] = (start, end)
            self.duration = end - start
            return self

        def write_videofile(self, path, **kwargs):
            calls["write_kwargs"] = kwargs

        def close(self):
            calls["final_closed"] = True

    monkeypatch.setattr(moviepy_editor, "VideoFileClip", _Video)
    monkeypatch.setattr(moviepy_editor, "AudioFileClip", lambda _path: _ExternalAudio())
    monkeypatch.setattr(moviepy_editor, "ColorClip", _ColorClip)
    monkeypatch.setattr(moviepy_editor, "ImageClip", _ImageClip)
    monkeypatch.setattr(moviepy_editor, "CompositeVideoClip", _CompositeVideoClip)

    base = tmp_path / "seedance.mp4"
    subtitle = tmp_path / "caption.srt"
    audio = tmp_path / "doubao.mp3"
    output = tmp_path / "burned.mp4"
    base.write_bytes(b"MP4")
    audio.write_bytes(b"MP3")
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nVISIBLE CAPTION\n",
        encoding="utf-8",
    )

    avatar_talk._burn_subtitles(
        base,
        subtitle,
        output,
        audio_path=audio,
        task_id="video-alpha",
        producer="Huading",
        propagate_id="tenant-alpha",
    )

    assert calls["freeze_kwargs"]["freeze_duration"] == pytest.approx(2.0)
    assert calls["canvas_duration"] == pytest.approx(3.0)
    assert calls["final_duration"] == pytest.approx(3.0)
    assert calls["set_audio"].duration == pytest.approx(3.0)
    assert calls["fadeout_duration"] == pytest.approx(0.3)
    assert calls["write_kwargs"]["audio"] is True
    assert calls["write_kwargs"]["codec"] == "libx264"
    assert calls["write_kwargs"]["audio_codec"] == "aac"
    assert calls["write_kwargs"]["audio_bitrate"] == "192k"
    assert "aigc_label=" in " ".join(calls["write_kwargs"]["ffmpeg_params"])
    assert calls["audio_closed"] is True


def test_burn_subtitles_uses_external_tts_audio_duration_when_video_is_longer(
    monkeypatch,
    tmp_path: Path,
):
    import moviepy.editor as moviepy_editor

    calls: dict[str, object] = {}

    class _ExternalAudio:
        duration = 2.0

        def fx(self, effect, duration):
            calls["fadeout_duration"] = duration
            return self

        def close(self):
            calls["audio_closed"] = True

    class _Video:
        w = 720
        h = 1280
        duration = 5.0
        audio = None

        def __init__(self, _path=None):
            pass

        def fx(self, effect, **kwargs):
            calls["freeze_kwargs"] = kwargs
            return self

        def resize(self, _scale):
            return self

        def set_position(self, _position):
            return self

        def close(self):
            calls["video_closed"] = True

    class _ColorClip:
        def __init__(self, _size, *, color, duration):
            calls["canvas_duration"] = duration

    class _ImageClip:
        def __init__(self, _array, *, transparent):
            pass

        def set_start(self, start):
            return self

        def set_duration(self, duration):
            return self

        def set_position(self, position):
            return self

    class _CompositeVideoClip:
        def __init__(self, clips, *, size):
            self.audio = None

        def set_duration(self, duration):
            calls["final_duration"] = duration
            return self

        def set_audio(self, audio):
            self.audio = audio
            return self

        def write_videofile(self, path, **kwargs):
            calls["write_kwargs"] = kwargs

        def close(self):
            calls["final_closed"] = True

    monkeypatch.setattr(moviepy_editor, "VideoFileClip", _Video)
    monkeypatch.setattr(moviepy_editor, "AudioFileClip", lambda _path: _ExternalAudio())
    monkeypatch.setattr(moviepy_editor, "ColorClip", _ColorClip)
    monkeypatch.setattr(moviepy_editor, "ImageClip", _ImageClip)
    monkeypatch.setattr(moviepy_editor, "CompositeVideoClip", _CompositeVideoClip)

    base = tmp_path / "seedance.mp4"
    subtitle = tmp_path / "caption.srt"
    audio = tmp_path / "doubao.mp3"
    output = tmp_path / "burned.mp4"
    base.write_bytes(b"MP4")
    audio.write_bytes(b"MP3")
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nVISIBLE CAPTION\n",
        encoding="utf-8",
    )

    avatar_talk._burn_subtitles(base, subtitle, output, audio_path=audio)

    assert "freeze_kwargs" not in calls
    assert calls["canvas_duration"] == pytest.approx(2.0)
    assert calls["final_duration"] == pytest.approx(2.0)
    assert calls["fadeout_duration"] == pytest.approx(0.3)
    assert calls["write_kwargs"]["audio"] is True
    assert calls["audio_closed"] is True


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
