from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from app.core.image_aspect_ratio import RequestedImageAspectRatio

EcomCutoutBackground = Literal["white", "transparent"]
EcomModelGender = Literal["female", "male", "any"]
EcomProductImagesMode = Literal["multi_angle", "multi_item"]
EcomReplicateOutputMode = Literal["main", "detail"]
_ECOM_MODEL_TEXT_LIMIT = 20_000


class EcomCutoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str = Field(min_length=1, max_length=36)
    background: EcomCutoutBackground = "white"
    aspect_ratio: RequestedImageAspectRatio = "1:1"
    apply_visible_label: bool = False


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

    source_asset_id: str | None = Field(default=None, min_length=1, max_length=36)
    product_asset_ids: list[str] | None = Field(default=None, min_length=1)
    model_asset_ids: list[str] | None = None
    product_images_mode: EcomProductImagesMode = "multi_item"
    gender: EcomModelGender
    style_id: str | None = Field(default=None, min_length=1, max_length=64)
    custom_style: str | None = Field(
        default=None,
        min_length=1,
        max_length=_ECOM_MODEL_TEXT_LIMIT,
    )
    extra_prompt: str | None = Field(default=None, max_length=_ECOM_MODEL_TEXT_LIMIT)
    aspect_ratio: RequestedImageAspectRatio = "1:1"
    apply_visible_label: bool = False

    @model_validator(mode="after")
    def _require_product_image(self) -> "EcomModelRequest":
        if self.style_id is not None and self.custom_style is not None:
            raise PydanticCustomError(
                "friendly_ecom_model_style_conflict",
                "预设风格与自定义风格不能同时选择",
            )
        if not self.product_asset_ids and self.source_asset_id is None:
            raise PydanticCustomError(
                "friendly_ecom_model_product_required",
                "请至少上传一张商品图",
            )
        total_images = len(self.resolved_product_asset_ids) + len(self.model_asset_ids or [])
        if total_images > 6:
            raise PydanticCustomError(
                "friendly_ecom_model_image_limit",
                "商品图与模特图合计最多 6 张",
            )
        return self

    @property
    def resolved_product_asset_ids(self) -> list[str]:
        if self.product_asset_ids:
            return self.product_asset_ids
        assert self.source_asset_id is not None
        return [self.source_asset_id]


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


class EcomPosterTemplate(BaseModel):
    id: str
    name: str


class EcomPosterTemplatesResponse(BaseModel):
    templates: list[EcomPosterTemplate]


class EcomPosterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str = Field(min_length=1, max_length=36)
    template_id: str = Field(min_length=1, max_length=64)
    title: str
    subtitle: str
    apply_visible_label: bool = False


class EcomPosterAccepted(BaseModel):
    task_id: str
    status: Literal["queued"] = "queued"


class EcomPosterBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EcomPosterRequest] = Field(min_length=1)


class EcomPosterBatchItem(BaseModel):
    task_id: str
    source_asset_id: str
    status: Literal["queued"] = "queued"


class EcomPosterBatchAccepted(BaseModel):
    batch_id: str
    tasks: list[EcomPosterBatchItem]


class EcomReplicateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_image_asset_ids: list[str] = Field(min_length=1)
    product_image_asset_ids: list[str] = Field(min_length=1, max_length=4)
    product_info: dict[str, object] = Field(default_factory=dict)
    selling_points: list[str] = Field(default_factory=list, max_length=8)
    output_mode: EcomReplicateOutputMode = "main"
    size: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _validate_reference_image_limit(self) -> "EcomReplicateRequest":
        if self.output_mode == "detail":
            limit = 12
            mode_label = "详情模式"
        else:
            limit = 5
            mode_label = "主图模式"
        if len(self.reference_image_asset_ids) > limit:
            raise PydanticCustomError(
                "friendly_ecom_replicate_reference_limit",
                f"{mode_label}最多 {limit} 张参考图",
            )
        return self


class EcomReplicatePlanOutput(BaseModel):
    id: str
    index: int
    theme: str
    reference_asset_id: str | None = None
    product_asset_id: str | None = None
    requested_size: str
    requested_aspect: str
    status: str
    prompt: str | None = None
    asset_id: str | None = None
    download_url: str | None = None
    actual_width: int | None = None
    actual_height: int | None = None


class EcomReplicatePlanPayload(BaseModel):
    outputs: list[EcomReplicatePlanOutput]
    reference_analysis_json: list[dict[str, object]]
    template_mapping_json: dict[str, object]
    generation_plan_json: dict[str, object]


class EcomReplicateAccepted(BaseModel):
    job_id: str
    status: Literal[
        "planning",
        "plan_ready",
        "generating",
        "completed",
        "partial_failed",
        "failed",
        "cancelled",
    ]
    output_mode: EcomReplicateOutputMode
    output_count: int
    total_credits: float
    credit_rate: float
    requested_size: str
    requested_aspect: str
    heartbeat_at: str | None = None
    plan: EcomReplicatePlanPayload


class EcomReplicateConfirmAccepted(BaseModel):
    job_id: str
    status: Literal["generating", "completed", "partial_failed", "failed", "cancelled"]
    output_count: int
    total_credits: float
