from pydantic import BaseModel


class BgmTrackRead(BaseModel):
    track_id: str
    name: str
    duration_sec: int
    preview_url: str
    license: str


class BgmLibraryResponse(BaseModel):
    items: list[BgmTrackRead]
