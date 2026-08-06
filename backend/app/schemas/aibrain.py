from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AIBrainTier = Literal["low", "mid", "high"]
TopupAmount = Literal[100, 500, 1000, 2000]


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None


class ChatAttachmentRead(BaseModel):
    asset_id: str
    asset_type: str
    mime_type: str
    download_url: str | None = None


class ChatMessageRead(BaseModel):
    id: str
    conversation_id: str
    role: Literal["user", "assistant"]
    content: str
    attachments: list[ChatAttachmentRead] = Field(default_factory=list)
    tier: AIBrainTier | None = None
    model: str | None = None
    status: Literal["pending", "completed", "failed"]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reserved_credits: float = 0.0
    charged_credits: float = 0.0
    created_at: datetime


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationRead(ConversationSummary):
    messages: list[ChatMessageRead] = Field(default_factory=list)


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]
    total: int


class ConversationDeletedResponse(BaseModel):
    deleted: bool


class ConversationClearResponse(BaseModel):
    deleted_count: int


class ChatMessageCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(default="", max_length=20_000)
    tier: AIBrainTier
    attachment_asset_ids: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        return value.strip()

    @field_validator("attachment_asset_ids")
    @classmethod
    def unique_attachment_ids(cls, value: list[str]) -> list[str]:
        if any(not str(asset_id).strip() for asset_id in value):
            raise ValueError("attachment asset ids must not be empty")
        if len(set(value)) != len(value):
            raise ValueError("attachment asset ids must be unique")
        return value

    @model_validator(mode="after")
    def require_content_or_attachment(self):
        if not self.content and not self.attachment_asset_ids:
            raise ValueError("content or an image attachment is required")
        return self


class ReasoningWalletRead(BaseModel):
    available_credits: float
    reserved_credits: float
    total_topup_credits: float
    total_spent_credits: float
    topup_options: list[int] = Field(default_factory=lambda: [100, 500, 1000, 2000])
    single_request_limit: int = 200


class ReasoningWalletTopupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: TopupAmount
    idempotency_key: UUID


class ChatMessageCreateResponse(BaseModel):
    user_message: ChatMessageRead
    assistant_message: ChatMessageRead
    wallet: ReasoningWalletRead
    cooldown_retry_after_seconds: int | None = Field(default=None, ge=1)
