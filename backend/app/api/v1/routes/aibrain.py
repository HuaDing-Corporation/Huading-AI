from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import DbSessionDependency, get_object_storage, require_permission
from app.db.models import User
from app.schemas.aibrain import (
    ChatMessageCreateRequest,
    ChatMessageCreateResponse,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationRead,
    ReasoningWalletRead,
    ReasoningWalletTopupRequest,
)
from app.schemas.response import ApiResponse, ok
from app.services.aibrain import (
    create_conversation,
    get_conversation,
    list_conversations,
    read_reasoning_wallet,
    send_chat_message,
    top_up_reasoning_wallet,
)
from app.services.storage.base import ObjectStorage

router = APIRouter()
AIBrainPermissionDependency = Depends(require_permission("aibrain:chat"))
ObjectStorageDependency = Depends(get_object_storage)


@router.get("/wallet", response_model=ApiResponse[ReasoningWalletRead])
def get_aibrain_wallet(
    request: Request,
    user: User = AIBrainPermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[ReasoningWalletRead]:
    return ok(request, read_reasoning_wallet(db, tenant_id=user.tenant_id))


@router.post("/wallet/topup", response_model=ApiResponse[ReasoningWalletRead])
def top_up_aibrain_wallet(
    request: Request,
    payload: ReasoningWalletTopupRequest,
    user: User = AIBrainPermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[ReasoningWalletRead]:
    return ok(
        request,
        top_up_reasoning_wallet(
            db,
            tenant_id=user.tenant_id,
            amount=payload.amount,
        ),
    )


@router.post(
    "/conversations",
    response_model=ApiResponse[ConversationRead],
    status_code=status.HTTP_201_CREATED,
)
def create_aibrain_conversation(
    request: Request,
    payload: ConversationCreateRequest,
    user: User = AIBrainPermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[ConversationRead]:
    return ok(
        request,
        create_conversation(db, user=user, title=payload.title),
    )


@router.get(
    "/conversations",
    response_model=ApiResponse[ConversationListResponse],
)
def list_aibrain_conversations(
    request: Request,
    user: User = AIBrainPermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[ConversationListResponse]:
    return ok(request, list_conversations(db, tenant_id=user.tenant_id))


@router.get(
    "/conversations/{conversation_id}",
    response_model=ApiResponse[ConversationRead],
)
def get_aibrain_conversation(
    request: Request,
    conversation_id: str,
    user: User = AIBrainPermissionDependency,
    db: Session = DbSessionDependency,
) -> ApiResponse[ConversationRead]:
    return ok(
        request,
        get_conversation(
            db,
            tenant_id=user.tenant_id,
            conversation_id=conversation_id,
        ),
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ApiResponse[ChatMessageCreateResponse],
)
async def create_aibrain_message(
    request: Request,
    conversation_id: str,
    payload: ChatMessageCreateRequest,
    user: User = AIBrainPermissionDependency,
    db: Session = DbSessionDependency,
    storage: ObjectStorage = ObjectStorageDependency,
) -> ApiResponse[ChatMessageCreateResponse]:
    return ok(
        request,
        await send_chat_message(
            db,
            user=user,
            conversation_id=conversation_id,
            content=payload.content,
            tier=payload.tier,
            attachment_asset_ids=payload.attachment_asset_ids,
            storage=storage,
        ),
    )
