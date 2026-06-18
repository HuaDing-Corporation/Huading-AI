from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "huading",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks", "app.workers.video_tasks", "app.workers.avatar_talk"],
)

celery_app.conf.update(
    task_default_queue="default",
    task_routes={
        "app.workers.tasks.*": {"queue": "default"},
        "app.workers.avatar_talk.generate": {"queue": "avatar"},
    },
    task_always_eager=settings.celery_task_always_eager,
    task_store_eager_result=False,
    result_extended=True,
    timezone="UTC",
)
