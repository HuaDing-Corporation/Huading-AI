from pydantic import BaseModel


class ComponentHealth(BaseModel):
    name: str
    status: str
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    components: list[ComponentHealth] = []
