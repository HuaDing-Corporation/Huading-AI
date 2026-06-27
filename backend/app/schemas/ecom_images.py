from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EcomCutoutBackground = Literal["white", "transparent"]
EcomModelGender = Literal["female", "male", "any"]


class EcomCutoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str = Field(min_length=1, max_length=36)
    background: EcomCutoutBackground = "white"


class EcomCutoutBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EcomCutoutRequest] = Field(min_length=1)


class EcomCutoutAccepted(BaseModel):
    task_id: str
    status: Literal["queued"] = "queued"


class EcomCutoutBatchItem(BaseModel):
    task_id: str
    source_asset_id: str
    status: Literal["queued"] = "queued"


class EcomCutoutBatchAccepted(BaseModel):
    batch_id: str
    tasks: list[EcomCutoutBatchItem]


class EcomModelStyle(BaseModel):
    id: str
    name: str


class EcomModelStylesResponse(BaseModel):
    styles: list[EcomModelStyle]


class EcomModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str = Field(min_length=1, max_length=36)
    gender: EcomModelGender
    style_id: str = Field(min_length=1, max_length=64)
    extra_prompt: str | None = None


class EcomModelAccepted(BaseModel):
    task_id: str
    status: Literal["queued"] = "queued"


class EcomModelBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EcomModelRequest] = Field(min_length=1)


class EcomModelBatchItem(BaseModel):
    task_id: str
    source_asset_id: str
    status: Literal["queued"] = "queued"


class EcomModelBatchAccepted(BaseModel):
    batch_id: str
    tasks: list[EcomModelBatchItem]
