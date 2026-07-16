from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.api.v1.routes.videos import stream_video_events
from app.db.models import Base, Tenant, VideoTask


class _UnusedProgressStore:
    def read(self, _task_id: str):
        raise AssertionError("The SSE body must stay unconsumed in this pool-lifetime test.")


def test_sse_releases_request_session_before_streaming(tmp_path) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'sse-pool.db').as_posix()}",
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as seed:
        tenant = Tenant(slug="sse-pool", name="SSE Pool")
        seed.add(tenant)
        seed.flush()
        task = VideoTask(
            tenant_id=tenant.id,
            status="running",
            mode="photo",
            video_mode="photo",
        )
        seed.add(task)
        seed.commit()
        tenant_id = tenant.id
        task_id = task.id

    request_session = session_factory()
    response = None
    try:
        response = asyncio.run(
            stream_video_events(
                task_id,
                user=SimpleNamespace(tenant_id=tenant_id),
                db=request_session,
                store=_UnusedProgressStore(),
            )
        )

        assert engine.pool.checkedout() == 0
    finally:
        if response is not None:
            asyncio.run(response.body_iterator.aclose())
        request_session.close()
        engine.dispose()
