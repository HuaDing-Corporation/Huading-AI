from celery.result import AsyncResult
from fastapi import APIRouter, Depends, Request, status

from app.api.deps import CurrentUserDependency, require_permission, scoped_task_id
from app.core.exceptions import AppError
from app.schemas.response import ApiResponse, ok
from app.schemas.tasks import DemoTaskRequest, TaskAccepted, TaskStatus
from app.workers.celery_app import celery_app
from app.workers.tasks import demo_echo

TaskPermissionDependency = Depends(require_permission("dev:access"))
router = APIRouter(dependencies=[CurrentUserDependency, TaskPermissionDependency])
_local_task_results: dict[str, TaskStatus] = {}
_local_task_tenants: dict[str, str] = {}


@router.post(
    "/demo",
    response_model=ApiResponse[TaskAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_demo_task(request: Request, payload: DemoTaskRequest) -> ApiResponse[TaskAccepted]:
    user = request.state.current_user
    result = demo_echo.delay(payload.message)
    _local_task_tenants[result.id] = user.tenant_id
    if celery_app.conf.task_always_eager and result.ready():
        _local_task_results[scoped_task_id(user.tenant_id, result.id)] = TaskStatus(
            task_id=result.id,
            status=result.status,
            result=result.result if result.successful() else None,
        )
    return ok(request, TaskAccepted(task_id=result.id, status=result.status))


@router.get("/{task_id}", response_model=ApiResponse[TaskStatus])
def get_task_status(request: Request, task_id: str) -> ApiResponse[TaskStatus]:
    user = request.state.current_user
    owner_tenant_id = _local_task_tenants.get(task_id)
    if owner_tenant_id is not None and owner_tenant_id != user.tenant_id:
        raise AppError("Task not found.", code="TASK_NOT_FOUND", status_code=404)

    scoped_id = scoped_task_id(user.tenant_id, task_id)
    if scoped_id in _local_task_results:
        return ok(request, _local_task_results[scoped_id])

    if owner_tenant_id is None:
        raise AppError("Task not found.", code="TASK_NOT_FOUND", status_code=404)

    result = AsyncResult(task_id, app=celery_app)
    return ok(
        request,
        TaskStatus(
            task_id=task_id,
            status=result.status,
            result=result.result if result.successful() else None,
        ),
    )
