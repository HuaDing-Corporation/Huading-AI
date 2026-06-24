from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "huading",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks",
        "app.workers.video_tasks",
        "app.workers.avatar_talk",
        "app.workers.image_gen",
    ],
)

celery_app.conf.update(
    task_default_queue="default",
    task_routes={
        "app.workers.tasks.*": {"queue": "default"},
        "app.workers.avatar_talk.generate": {"queue": "avatar"},
        "app.workers.avatar_talk.generate_seedance_i2v": {"queue": "avatar"},
        "app.workers.image_gen.generate": {"queue": "image"},
    },
    task_always_eager=settings.celery_task_always_eager,
    task_store_eager_result=False,
    result_extended=True,
    timezone="UTC",
)
