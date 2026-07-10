import math
from typing import Literal, TypeAlias, cast

ImageAspectRatio: TypeAlias = Literal[
    "1:1",
    "4:3",
    "3:2",
    "16:9",
    "21:9",
    "3:4",
    "2:3",
    "9:16",
]
RequestedImageAspectRatio: TypeAlias = Literal[
    "1:1",
    "4:3",
    "3:2",
    "16:9",
    "21:9",
    "3:4",
    "2:3",
    "9:16",
    "auto",
]

IMAGE_ASPECT_RATIOS: tuple[ImageAspectRatio, ...] = (
    "1:1",
    "4:3",
    "3:2",
    "16:9",
    "21:9",
    "3:4",
    "2:3",
    "9:16",
)
VIDEO_ASPECT_RATIOS = frozenset({"9:16", "16:9", "1:1"})
LEGACY_IMAGE_SIZE_ASPECT_RATIOS: dict[str, ImageAspectRatio] = {
    "1024x1024": "1:1",
    "1536x1024": "3:2",
    "1024x1536": "2:3",
}
_ASPECT_RATIO_VALUES = {
    ratio: int(ratio.split(":", 1)[0]) / int(ratio.split(":", 1)[1])
    for ratio in IMAGE_ASPECT_RATIOS
}
_OPENAI_IMAGE_SIZES: dict[ImageAspectRatio, str] = {
    "1:1": "1024x1024",
    "4:3": "1536x1024",
    "3:2": "1536x1024",
    "16:9": "1536x1024",
    "21:9": "1536x1024",
    "3:4": "1024x1536",
    "2:3": "1024x1536",
    "9:16": "1024x1536",
}


def image_aspect_ratio_from_legacy_size(image_size: str | None) -> ImageAspectRatio:
    return LEGACY_IMAGE_SIZE_ASPECT_RATIOS.get(str(image_size or "").strip(), "1:1")


def closest_image_aspect_ratio(width: int, height: int) -> ImageAspectRatio:
    if width <= 0 or height <= 0:
        return "1:1"
    actual = width / height
    return min(
        IMAGE_ASPECT_RATIOS,
        key=lambda ratio: abs(math.log(actual / _ASPECT_RATIO_VALUES[ratio])),
    )


def resolve_image_aspect_ratio(
    requested: str,
    *,
    width: int | None = None,
    height: int | None = None,
) -> ImageAspectRatio:
    if requested == "auto":
        if width is None or height is None:
            return "1:1"
        return closest_image_aspect_ratio(width, height)
    if requested in IMAGE_ASPECT_RATIOS:
        return cast(ImageAspectRatio, requested)
    return "1:1"


def openai_image_size(aspect_ratio: str) -> str:
    resolved = resolve_image_aspect_ratio(aspect_ratio)
    return _OPENAI_IMAGE_SIZES[resolved]


def aspect_ratio_from_provider_size(size: str) -> ImageAspectRatio:
    normalized = str(size or "").strip().lower()
    if normalized in IMAGE_ASPECT_RATIOS:
        return cast(ImageAspectRatio, normalized)
    parts = normalized.split("x", 1)
    if len(parts) != 2:
        return "1:1"
    try:
        return closest_image_aspect_ratio(int(parts[0]), int(parts[1]))
    except ValueError:
        return "1:1"
