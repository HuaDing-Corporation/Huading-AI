from typing import Literal

from pydantic import BaseModel, Field, field_validator

LabelPosition = Literal["br", "bl", "tr", "tl", "bc"]


class TenantLabelSettingsRead(BaseModel):
    position: LabelPosition
    text: str
    enabled: bool = True


class TenantLabelSettingsUpdate(BaseModel):
    position: LabelPosition
    text: str = Field(min_length=1, max_length=20)
    enabled: bool | None = None

    @field_validator("text")
    @classmethod
    def strip_nonempty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Label text must not be empty.")
        return text
