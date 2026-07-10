from __future__ import annotations

from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.reverse_prompt.generate_video")
def generate_reverse_prompt_video_task(job_id: str) -> dict[str, str]:
    from app.services.reverse_prompt_video import run_reverse_prompt_video_job

    return run_reverse_prompt_video_job(job_id)
