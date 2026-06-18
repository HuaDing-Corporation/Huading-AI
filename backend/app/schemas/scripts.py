from pydantic import BaseModel, ConfigDict, Field


class ScriptGenerateRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)

    model_config = ConfigDict(extra="forbid")


class ScriptGenerateResponse(BaseModel):
    script: str
