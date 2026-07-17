from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ImageHistoryCategory = Literal[
    "image_gen",
    "ecom_white",
    "ecom_model",
    "ecom_detail",
    "cover",
]


class ImageHistoryItem(BaseModel):
    id: str
    category: ImageHistoryCategory
    title: str
    cover_url: str
    created_at: datetime
    status: str
    item_count: int


class ImageHistoryListResponse(BaseModel):
    items: list[ImageHistoryItem]
    total: int
    page: int
    page_size: int


class ImageHistoryDetailItem(BaseModel):
    index: int
    download_url: str
    width: int = Field(
        deprecated=True,
        description="Deprecated compatibility value; use actual_width for output pixels.",
    )
    height: int = Field(
        deprecated=True,
        description="Deprecated compatibility value; use actual_height for output pixels.",
    )
    requested_width: int | None
    requested_height: int | None
    actual_width: int | None
    actual_height: int | None
    requested_aspect_ratio: str | None = None
    resolved_aspect_ratio: str | None = None
    resolved_size: str | None = None
    actual_aspect_ratio: str | None = None
    actual_size: str | None = None
    theme: str | None = None
    label: str | None = None


class ImageHistoryDetailResponse(BaseModel):
    id: str
    category: ImageHistoryCategory
    created_at: datetime
    status: str
    items: list[ImageHistoryDetailItem]
    meta: dict[str, object]
