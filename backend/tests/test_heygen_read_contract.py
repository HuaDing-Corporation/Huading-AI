import json
from datetime import UTC, datetime

import pytest

from app.api.v1.routes.videos import _sse_payload, _video_read
from app.db.models import VideoTask


def test_heygen_db_hold_overrides_stale_failure_snapshot():
    task = VideoTask(
        id="held-task",
        tenant_id="tenant",
        mode="avatar_talk",
        status="running",
        progress=25,
        created_at=datetime.now(UTC),
        params={"avatar_provider": "heygen", "avatar_model": "avatar_iv"},
        error_code="HEYGEN_PENDING",
        error_message="Reconciling",
    )
    result = _video_read(task, storage=None, snapshot={"status": "failed", "progress": 25})
    assert result.status == "running"
    assert result.avatar_provider == "heygen" and result.avatar_model == "avatar_iv"
    assert result.error_code == "HEYGEN_PENDING"


def test_sse_includes_hold_and_explicit_clear_without_private_request():
    result = _sse_payload(
        "held-task",
        {
            "status": "running",
            "progress": 25,
            "avatar_provider": "heygen",
            "avatar_model": "avatar_iv",
            "error_code": "HEYGEN_PENDING",
            "error_message": "Reconciling",
            "request_body": {"private": "do not disclose"},
        },
    )
    assert result["error_code"] == "HEYGEN_PENDING"
    assert result["avatar_provider"] == "heygen"
    assert "request_body" not in result
    cleared = _sse_payload(
        "held-task",
        {
            "status": "running",
            "progress": 26,
            "avatar_provider": "heygen",
            "error_code": "",
            "error_message": "",
        },
    )
    assert "error_code" in cleared and cleared["error_code"] is None


@pytest.mark.asyncio
async def test_real_sse_reopens_session_and_clears_hold_from_fresh_database(
    auth_db,
    auth_context,
    monkeypatch,
):
    from app.api.v1.routes import videos
    from app.db.models import User

    with auth_db() as seed:
        task = VideoTask(
            tenant_id=auth_context["tenant_id"],
            status="running",
            mode="avatar_talk",
            video_mode="avatar_talk",
            params={"avatar_provider": "heygen", "avatar_model": "avatar_iv"},
            error_code="HEYGEN_PENDING",
        )
        seed.add(task)
        seed.commit()
        task_id = task.id
    sleeps = 0

    async def advance(_seconds):
        nonlocal sleeps
        sleeps += 1
        with auth_db() as other:
            task = other.get(VideoTask, task_id)
            task.error_code = None
            task.error_message = None
            if sleeps == 2:
                task.status = "done"
                task.progress = 100
            other.commit()

    class StaleStore:
        def read(self, _key):
            return {"status": "failed", "error_code": "OLD_FAILURE", "progress": 25}

    monkeypatch.setattr(videos.asyncio, "sleep", advance)
    with auth_db() as db:
        user = db.get(User, auth_context["user_id"])
        response = await videos.stream_video_events(
            task_id, user=user, db=db, store=StaleStore(), storage=None
        )
        frames = [
            json.loads(frame.removeprefix("data: ")) async for frame in response.body_iterator
        ]
    assert [frame["status"] for frame in frames] == ["running", "running", "done"]
    assert [frame["error_code"] for frame in frames] == ["HEYGEN_PENDING", None, None]
