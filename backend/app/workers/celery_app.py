from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "huading",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.task_routes = {"app.workers.tasks.*": {"queue": "default"}}
