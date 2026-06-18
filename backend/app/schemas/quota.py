from pydantic import BaseModel


class QuotaResponse(BaseModel):
    total: int
    used: int
    reserved: int
    remaining: int
