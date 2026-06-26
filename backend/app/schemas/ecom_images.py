from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EcomCutoutBackground = Literal["white", "transparent"]


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
