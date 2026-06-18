from pydantic import BaseModel


class UploadResponse(BaseModel):
    """A stored upload. ``key`` is tenant-relative (e.g. ``uploads/<uuid>.jpg``)
    and is what i2v video requests reference as ``image_key``."""

    key: str
    uri: str
    content_type: str
    size: int


class UploadImageResponse(BaseModel):
    asset_id: str
    type: str
    status: str
    thumbnail_url: str | None = None
