from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Protocol

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

GENERATION_HEARTBEAT_THREAD_PREFIX = "generation-heartbeat:"


class HeartbeatProgressStore(Protocol):
    def update(self, task_id: str, **fields: object) -> None: ...


def _heartbeat_loop(
    store: HeartbeatProgressStore,
    *,
    task_id: str,
    interval_seconds: float,
    stopped: threading.Event,
) -> None:
    while not stopped.wait(interval_seconds):
        try:
            store.update(task_id, heartbeat_at=datetime.now(UTC).isoformat())
        except Exception as exc:
            logger.warning(
                "generation_heartbeat_publish_failed",
                task_id=task_id,
                error=str(exc),
            )


@contextmanager
def generation_heartbeat(
    store: HeartbeatProgressStore,
    *,
    task_id: str,
    interval_seconds: float | None = None,
) -> Iterator[None]:
    """Publish liveness timestamps while a synchronous generation call blocks."""

    interval = (
        settings.engine_gen_heartbeat_interval_seconds
        if interval_seconds is None
        else interval_seconds
    )
    if interval <= 0:
        raise ValueError("Generation heartbeat interval must be positive.")

    stopped = threading.Event()
    thread = threading.Thread(
        target=_heartbeat_loop,
        kwargs={
            "store": store,
            "task_id": task_id,
            "interval_seconds": interval,
            "stopped": stopped,
        },
        name=f"{GENERATION_HEARTBEAT_THREAD_PREFIX}{task_id}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join()
