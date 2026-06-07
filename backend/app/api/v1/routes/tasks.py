from celery.result import AsyncResult
from fastapi import APIRouter, Request, status

from app.schemas.response import ApiResponse, ok
from app.schemas.tasks import DemoTaskRequest, TaskAccepted, TaskStatus
from app.workers.celery_app import celery_app
from app.workers.tasks import demo_echo

router = APIRouter()
_local_task_results: dict[str, TaskStatus] = {}


@router.post(
    "/demo",
    response_model=ApiResponse[TaskAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_demo_task(request: Request, payload: DemoTaskRequest) -> ApiResponse[TaskAccepted]:
    result = demo_echo.delay(payload.message)
    if celery_app.conf.task_always_eager and result.ready():
        _local_task_results[result.id] = TaskStatus(
            task_id=result.id,
            status=result.status,
            result=result.result if result.successful() else None,
        )
    return ok(request, TaskAccepted(task_id=result.id, status=result.status))


@router.get("/{task_id}", response_model=ApiResponse[TaskStatus])
def get_task_status(request: Request, task_id: str) -> ApiResponse[TaskStatus]:
    if task_id in _local_task_results:
        return ok(request, _local_task_results[task_id])

    result = AsyncResult(task_id, app=celery_app)
    return ok(
        request,
        TaskStatus(
            task_id=task_id,
            status=result.status,
            result=result.result if result.successful() else None,
        ),
    )
