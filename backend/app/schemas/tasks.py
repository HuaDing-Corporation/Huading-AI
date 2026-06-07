from pydantic import BaseModel, Field


class DemoTaskRequest(BaseModel):
    message: str = Field(default="hello", min_length=1, max_length=256)


class TaskAccepted(BaseModel):
    task_id: str
    status: str


class TaskStatus(BaseModel):
    task_id: str
    status: str
    result: object | None = None
