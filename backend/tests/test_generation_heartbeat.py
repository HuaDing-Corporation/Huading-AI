from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

from app.db import session as db_session
from app.services.generation_heartbeat import (
    GENERATION_HEARTBEAT_THREAD_PREFIX,
    generation_heartbeat,
)
from app.services.progress import ProgressStore


class _RecordingStore:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.data = {
            "tenant:task": {
                "task_id": "tenant:task",
                "status": "running",
                "progress": 30,
                "stage": "generating",
                "step": "image_provider",
            }
        }
        self.events: list[tuple[float, dict[str, object]]] = []

    def update(self, task_id: str, **fields: object) -> None:
        with self.condition:
            snapshot = dict(self.data.get(task_id, {}))
            snapshot.update(fields)
            snapshot["task_id"] = task_id
            self.data[task_id] = snapshot
            self.events.append((time.perf_counter(), snapshot))
            self.condition.notify_all()

    def wait_for_events(self, count: int, *, timeout: float) -> bool:
        with self.condition:
            return self.condition.wait_for(lambda: len(self.events) >= count, timeout=timeout)


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, *, ex: int) -> None:
        assert ex > 0
        self.data[key] = value


def _heartbeat_threads(task_id: str) -> list[threading.Thread]:
    expected_name = f"{GENERATION_HEARTBEAT_THREAD_PREFIX}{task_id}"
    return [thread for thread in threading.enumerate() if thread.name == expected_name]


def test_generation_heartbeat_emits_periodically_without_changing_progress(monkeypatch) -> None:
    store = _RecordingStore()
    interval_seconds = 0.03

    def fail_if_db_is_touched(*_args, **_kwargs):
        raise AssertionError("heartbeat thread must not create or use a DB session")

    monkeypatch.setattr(db_session, "SessionLocal", fail_if_db_is_touched)

    with generation_heartbeat(
        store,
        task_id="tenant:task",
        interval_seconds=interval_seconds,
    ):
        assert store.wait_for_events(3, timeout=2.0)

    assert len(store.events) >= 3
    gaps = [
        later[0] - earlier[0]
        for earlier, later in zip(store.events, store.events[1:], strict=False)
    ]
    assert gaps
    assert all(gap >= interval_seconds * 0.6 for gap in gaps)
    for _emitted_at, snapshot in store.events:
        assert snapshot["progress"] == 30
        assert snapshot["stage"] == "generating"
        assert snapshot["step"] == "image_provider"
        assert datetime.fromisoformat(str(snapshot["heartbeat_at"])).tzinfo is not None


@pytest.mark.parametrize("exit_kind", ["return", "exception", "system_exit"])
def test_generation_heartbeat_stops_thread_for_every_exit(exit_kind: str) -> None:
    store = _RecordingStore()
    task_id = f"tenant:{exit_kind}"

    def run() -> None:
        with generation_heartbeat(store, task_id=task_id, interval_seconds=0.01):
            time.sleep(0.025)
            if exit_kind == "exception":
                raise RuntimeError("provider failed")
            if exit_kind == "system_exit":
                raise SystemExit("worker terminated")

    expected_error = {
        "return": None,
        "exception": RuntimeError,
        "system_exit": SystemExit,
    }[exit_kind]
    if expected_error is None:
        run()
    else:
        with pytest.raises(expected_error):
            run()

    assert _heartbeat_threads(task_id) == []


def test_progress_store_round_trips_heartbeat_at() -> None:
    redis = _FakeRedis()
    store = ProgressStore(redis)  # type: ignore[arg-type]

    store.update(
        "tenant:task",
        status="running",
        progress=30,
        stage="generating",
        heartbeat_at="2026-07-25T08:00:00+00:00",
    )

    assert store.read("tenant:task") == {
        "task_id": "tenant:task",
        "status": "running",
        "progress": 30,
        "stage": "generating",
        "heartbeat_at": "2026-07-25T08:00:00+00:00",
    }
