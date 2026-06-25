from pydantic import BaseModel


class SubtitleTemplate(BaseModel):
    id: str
    name: str
    font_family: str
    font_size: int
    color: str
    stroke_color: str | None = None
    stroke_width: int
    background: str | None = None
    position: str


class SubtitleTemplateList(BaseModel):
    templates: list[SubtitleTemplate]
