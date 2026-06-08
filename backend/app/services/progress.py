"""Redis-backed progress store for async video generation tasks.

The Celery worker publishes progress here from the engine's progress callback;
the API reads it for GET /videos/{id} and the SSE stream. Keyed by task_id with
a TTL so finished tasks expire on their own.
"""

from __future__ import annotations

import json
from typing import Any

import redis

from app.core.config import settings

_TTL_SECONDS = 24 * 3600
_KNOWN_FIELDS = {
    "task_id",
    "status",
    "stage",
    "progress",
    "frame_current",
    "frame_total",
    "video_url",
    "error",
}


def _key(task_id: str) -> str:
    return f"video:progress:{task_id}"


class ProgressStore:
    """Thin JSON-over-Redis store. One hash-like blob per task_id."""

    def __init__(self, client: redis.Redis) -> None:
        self._redis = client

    def update(self, task_id: str, **fields: Any) -> None:
        snapshot = self.read(task_id) or {}
        for name, value in fields.items():
            if value is not None:
                snapshot[name] = value
        snapshot["task_id"] = task_id
        self._redis.set(_key(task_id), json.dumps(snapshot), ex=_TTL_SECONDS)

    def read(self, task_id: str) -> dict[str, Any] | None:
        raw = self._redis.get(task_id and _key(task_id))
        if not raw:
            return None
        data = json.loads(raw)
        # Drop any unexpected keys so it maps cleanly onto VideoTaskStatus.
        return {k: v for k, v in data.items() if k in _KNOWN_FIELDS}


def build_progress_store(redis_url: str) -> ProgressStore:
    # Socket timeouts so a Redis outage degrades fast (e.g. GET /videos falls
    # back / returns) instead of hanging the request or worker (#003-FIX P2).
    return ProgressStore(
        redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_connect_timeout,
            socket_timeout=settings.redis_socket_timeout,
        )
    )
