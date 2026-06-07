from pydantic import BaseModel, Field


class StoragePutRequest(BaseModel):
    key: str = Field(min_length=1, max_length=512)
    content: str
    content_type: str = "text/plain; charset=utf-8"


class StoragePutResponse(BaseModel):
    key: str
    uri: str
