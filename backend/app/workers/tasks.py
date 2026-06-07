from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="app.workers.tasks.demo_echo")
def demo_echo(message: str) -> dict[str, str]:
    return {"message": message, "status": "processed"}
