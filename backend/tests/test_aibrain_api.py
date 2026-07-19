import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_object_storage
from app.db.models import (
    Asset,
    ChatMessage,
    ReasoningLedgerEntry,
    ReasoningWallet,
    Subscription,
    UsageRecord,
    User,
)
from app.main import app
from app.services import aibrain


class _FakeChatProvider:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def chat(self, payload: dict) -> dict:
        self.calls.append(payload)
        return self.result


class _FailingChatProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat(self, payload: dict) -> dict:
        self.calls.append(payload)
        raise RuntimeError("mock upstream failure")


class _CancelledChatProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat(self, payload: dict) -> dict:
        self.calls.append(payload)
        raise asyncio.CancelledError


class _FakeObjectStorage:
    bucket = "test-bucket"

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        assert expires_in > 0
        assert download_filename is None
        return f"https://storage.example/{key}"


def test_user_can_create_list_and_read_an_aibrain_conversation(
    auth_context,
) -> None:
    client = TestClient(app)

    created = client.post(
        "/api/v1/aibrain/conversations",
        json={"title": "Launch planning"},
        headers=auth_context["headers"],
    )

    assert created.status_code == 201
    conversation = created.json()["data"]
    assert conversation["title"] == "Launch planning"
    assert conversation["messages"] == []

    listed = client.get(
        "/api/v1/aibrain/conversations",
        headers=auth_context["headers"],
    )
    assert listed.status_code == 200
    assert listed.json()["data"] == {
        "items": [
            {
                "id": conversation["id"],
                "title": "Launch planning",
                "created_at": conversation["created_at"],
                "updated_at": conversation["updated_at"],
            }
        ],
        "total": 1,
    }

    detail = client.get(
        f"/api/v1/aibrain/conversations/{conversation['id']}",
        headers=auth_context["headers"],
    )
    assert detail.status_code == 200
    assert detail.json()["data"] == conversation


def test_user_can_top_up_the_reasoning_wallet_from_primary_quota(
    auth_context,
    auth_db,
) -> None:
    client = TestClient(app)

    empty = client.get(
        "/api/v1/aibrain/wallet",
        headers=auth_context["headers"],
    )
    assert empty.status_code == 200
    assert empty.json()["data"]["available_credits"] == 0

    topped_up = client.post(
        "/api/v1/aibrain/wallet/topup",
        json={"amount": 500},
        headers=auth_context["headers"],
    )

    assert topped_up.status_code == 200
    assert topped_up.json()["data"] == {
        "available_credits": 500.0,
        "reserved_credits": 0.0,
        "total_topup_credits": 500.0,
        "total_spent_credits": 0.0,
        "topup_options": [100, 500, 1000, 2000],
        "single_request_limit": 200,
    }
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        ledger = db.scalar(
            select(ReasoningLedgerEntry).where(
                ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"]
            )
        )
        assert subscription.quota_credits_used == 500
        assert wallet.available_credits == 500
        assert wallet.reserved_credits == 0
        assert ledger.entry_type == "topup"
        assert ledger.amount_credits == 500
        assert ledger.subscription_id == subscription.id


def test_message_reserves_then_settles_exact_token_usage(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    assert (
        client.post(
            "/api/v1/aibrain/wallet/topup",
            json={"amount": 500},
            headers=auth_context["headers"],
        ).status_code
        == 200
    )
    conversation = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]
    provider = _FakeChatProvider(
        {
            "content": "Use a three-act launch plan.",
            "model": "gpt-5.6-terra",
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider, raising=False)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation['id']}/messages",
        json={"content": "Plan this launch", "tier": "mid"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["user_message"]["content"] == "Plan this launch"
    assert data["assistant_message"]["content"] == "Use a three-act launch plan."
    assert data["assistant_message"]["model"] == "gpt-5.6-terra"
    assert data["assistant_message"]["prompt_tokens"] == 1000
    assert data["assistant_message"]["completion_tokens"] == 500
    assert data["assistant_message"]["charged_credits"] == 17.28
    assert data["wallet"]["available_credits"] == 482.72
    assert data["wallet"]["reserved_credits"] == 0
    assert len(provider.calls) == 1
    assert provider.calls[0]["model"] == "gpt-5.6-terra"
    assert provider.calls[0]["messages"][-1] == {
        "role": "user",
        "content": "Plan this launch",
    }

    with auth_db() as db:
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        entries = list(
            db.scalars(
                select(ReasoningLedgerEntry)
                .where(ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"])
                .order_by(ReasoningLedgerEntry.created_at.asc())
            )
        )
        usage = db.scalar(
            select(UsageRecord).where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.capability == "chat",
            )
        )
        assistant = db.scalar(
            select(ChatMessage).where(ChatMessage.id == data["assistant_message"]["id"])
        )
        assert wallet.available_credits == Decimal("482.720000")
        assert wallet.reserved_credits == Decimal("0")
        assert [entry.entry_type for entry in entries] == ["topup", "reserve", "settle"]
        assert entries[1].amount_credits == Decimal("200")
        assert entries[2].amount_credits == Decimal("17.280000")
        assert usage.credits == Decimal("17.280000")
        assert usage.provider_cost_usd == Decimal("0.00800000")
        assert usage.chat_message_id == assistant.id
        assert sum(entry.available_delta for entry in entries) == wallet.available_credits
        assert sum(entry.reserved_delta for entry in entries) == wallet.reserved_credits
        assert wallet.available_credits + wallet.total_spent_credits == wallet.total_topup_credits


def test_insufficient_reasoning_balance_never_calls_provider_or_reserves(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FakeChatProvider(
        {
            "content": "must not be returned",
            "model": "gpt-5.6-luna",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Will this run?", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 402
    assert response.json()["error"]["code"] == "AIBRAIN_INSUFFICIENT_BALANCE"
    assert provider.calls == []
    with auth_db() as db:
        assert db.get(ReasoningWallet, auth_context["tenant_id"]) is None
        assert db.scalar(select(ReasoningLedgerEntry.id)) is None
        assert db.scalar(select(ChatMessage.id)) is None


def test_single_answer_charge_is_capped_at_200_reasoning_credits(
    auth_context,
    monkeypatch,
) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/aibrain/wallet/topup",
        json={"amount": 500},
        headers=auth_context["headers"],
    )
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FakeChatProvider(
        {
            "content": "An intentionally expensive mock answer.",
            "model": "gpt-5.6-sol",
            "prompt_tokens": 10_000,
            "completion_tokens": 10_000,
            "total_tokens": 20_000,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Spend no more than the cap", "tier": "high"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["assistant_message"]["charged_credits"] == 200.0
    assert data["assistant_message"]["reserved_credits"] == 200.0
    assert data["wallet"]["available_credits"] == 300.0
    assert data["wallet"]["reserved_credits"] == 0.0


def test_provider_failure_releases_reservation_without_user_charge(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/aibrain/wallet/topup",
        json={"amount": 500},
        headers=auth_context["headers"],
    )
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FailingChatProvider()
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Fail upstream", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "AIBRAIN_PROVIDER_FAILED"
    assert len(provider.calls) == 1
    with auth_db() as db:
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        entries = list(
            db.scalars(
                select(ReasoningLedgerEntry)
                .where(ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"])
                .order_by(ReasoningLedgerEntry.created_at.asc())
            )
        )
        usage = db.scalar(select(UsageRecord).where(UsageRecord.capability == "chat"))
        failed_message = db.scalar(select(ChatMessage).where(ChatMessage.status == "failed"))
        assert wallet.available_credits == Decimal("500")
        assert wallet.reserved_credits == Decimal("0")
        assert [entry.entry_type for entry in entries] == ["topup", "reserve", "release"]
        assert usage.status == "released"
        assert usage.credits == Decimal("0")
        assert usage.provider_cost_usd > 0
        assert failed_message.error_code == "AIBRAIN_PROVIDER_FAILED"


def test_missing_usage_releases_reservation_without_a_second_provider_call(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/aibrain/wallet/topup",
        json={"amount": 100},
        headers=auth_context["headers"],
    )
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FakeChatProvider(
        {
            "content": "Unbillable answer",
            "model": "gpt-5.6-luna",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Return no usage", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "AIBRAIN_USAGE_MISSING"
    assert len(provider.calls) == 1
    with auth_db() as db:
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        assert wallet.available_credits == Decimal("100")
        assert wallet.reserved_credits == Decimal("0")


@pytest.mark.asyncio
async def test_cancelled_provider_call_releases_the_reasoning_reservation(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/aibrain/wallet/topup",
        json={"amount": 100},
        headers=auth_context["headers"],
    )
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _CancelledChatProvider()
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    with auth_db() as db:
        user = db.get(User, auth_context["user_id"])
        with pytest.raises(asyncio.CancelledError):
            await aibrain.send_chat_message(
                db,
                user=user,
                conversation_id=conversation_id,
                content="Cancel this request",
                tier="low",
                attachment_asset_ids=[],
                storage=_FakeObjectStorage(),
            )

    assert len(provider.calls) == 1
    with auth_db() as db:
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        entries = list(
            db.scalars(
                select(ReasoningLedgerEntry)
                .where(ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"])
                .order_by(ReasoningLedgerEntry.created_at.asc())
            )
        )
        assert wallet.available_credits == Decimal("100")
        assert wallet.reserved_credits == Decimal("0")
        assert [entry.entry_type for entry in entries] == ["topup", "reserve", "release"]


def test_only_the_latest_20_completed_rounds_are_sent_as_context(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/aibrain/wallet/topup",
        json={"amount": 500},
        headers=auth_context["headers"],
    )
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={"title": "Long context"},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    started_at = datetime(2026, 7, 19, tzinfo=UTC)
    with auth_db() as db:
        for index in range(21):
            db.add_all(
                [
                    ChatMessage(
                        id=str(uuid4()),
                        tenant_id=auth_context["tenant_id"],
                        conversation_id=conversation_id,
                        role="user",
                        content=f"user-{index:02d}",
                        attachments=[],
                        tier="low",
                        model="gpt-5.6-luna",
                        status="completed",
                        created_at=started_at + timedelta(seconds=index * 2),
                    ),
                    ChatMessage(
                        id=str(uuid4()),
                        tenant_id=auth_context["tenant_id"],
                        conversation_id=conversation_id,
                        role="assistant",
                        content=f"assistant-{index:02d}",
                        attachments=[],
                        tier="low",
                        model="gpt-5.6-luna",
                        status="completed",
                        created_at=started_at + timedelta(seconds=index * 2 + 1),
                    ),
                ]
            )
        db.commit()
    provider = _FakeChatProvider(
        {
            "content": "Latest context used.",
            "model": "gpt-5.6-luna",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "current", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    sent_messages = provider.calls[0]["messages"]
    assert len(sent_messages) == 42  # system + 40 historical messages + current
    sent_text = [message["content"] for message in sent_messages]
    assert "user-00" not in sent_text
    assert "assistant-00" not in sent_text
    assert sent_text[1:3] == ["user-01", "assistant-01"]
    assert sent_text[-1] == "current"


def test_bare_model_name_is_rejected_before_provider_or_wallet_mutation(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FakeChatProvider({})
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Do not accept model names", "tier": "gpt-5.6-sol"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    assert provider.calls == []
    with auth_db() as db:
        assert db.scalar(select(ReasoningLedgerEntry.id)) is None


def test_ready_tenant_image_is_sent_as_a_presigned_vision_attachment(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/aibrain/wallet/topup",
        json={"amount": 100},
        headers=auth_context["headers"],
    )
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    with auth_db() as db:
        image = Asset(
            tenant_id=auth_context["tenant_id"],
            type="product_image",
            source="upload",
            storage_key=f"tenants/{auth_context['tenant_id']}/uploads/product.png",
            mime_type="image/png",
            status="ready",
        )
        db.add(image)
        db.commit()
        image_id = image.id
    provider = _FakeChatProvider(
        {
            "content": "The product is green ceramic.",
            "model": "gpt-5.6-luna",
            "prompt_tokens": 200,
            "completion_tokens": 80,
            "total_tokens": 280,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)
    app.dependency_overrides[get_object_storage] = _FakeObjectStorage
    try:
        response = client.post(
            f"/api/v1/aibrain/conversations/{conversation_id}/messages",
            json={
                "content": "Describe this product",
                "tier": "low",
                "attachment_asset_ids": [image_id],
            },
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    assert response.json()["data"]["user_message"]["attachments"] == [
        {
            "asset_id": image_id,
            "asset_type": "product_image",
            "mime_type": "image/png",
        }
    ]
    assert provider.calls[0]["messages"][-1] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe this product"},
            {
                "type": "image_url",
                "image_url": {
                    "url": (
                        f"https://storage.example/tenants/{auth_context['tenant_id']}"
                        "/uploads/product.png"
                    )
                },
            },
        ],
    }


def test_conversations_wallets_and_attachments_are_tenant_isolated(
    auth_context,
    auth_db,
    seed_plan,
    monkeypatch,
) -> None:
    client = TestClient(app)
    own_conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={"title": "Tenant A private"},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    client.post(
        "/api/v1/aibrain/wallet/topup",
        json={"amount": 100},
        headers=auth_context["headers"],
    )
    tenant_b = client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": "tenant-b-aibrain",
            "tenant_name": "Tenant B",
            "email": "owner-b@example.com",
            "password": "secret-pass",
        },
    ).json()["data"]
    tenant_b_headers = {"Authorization": f"Bearer {tenant_b['token']['access_token']}"}
    with auth_db() as db:
        foreign_image = Asset(
            tenant_id=tenant_b["tenant"]["id"],
            type="product_image",
            source="upload",
            storage_key=(f"tenants/{tenant_b['tenant']['id']}/uploads/private-product.png"),
            mime_type="image/png",
            status="ready",
        )
        db.add(foreign_image)
        db.commit()
        foreign_image_id = foreign_image.id
    provider = _FakeChatProvider({})
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    leaked_conversation = client.get(
        f"/api/v1/aibrain/conversations/{own_conversation_id}",
        headers=tenant_b_headers,
    )
    tenant_b_wallet = client.get(
        "/api/v1/aibrain/wallet",
        headers=tenant_b_headers,
    )
    foreign_attachment = client.post(
        f"/api/v1/aibrain/conversations/{own_conversation_id}/messages",
        json={
            "content": "Read another tenant image",
            "tier": "low",
            "attachment_asset_ids": [foreign_image_id],
        },
        headers=auth_context["headers"],
    )

    assert leaked_conversation.status_code == 404
    assert leaked_conversation.json()["error"]["code"] == "AIBRAIN_CONVERSATION_NOT_FOUND"
    assert tenant_b_wallet.status_code == 200
    assert tenant_b_wallet.json()["data"]["available_credits"] == 0
    assert foreign_attachment.status_code == 404
    assert foreign_attachment.json()["error"]["code"] == "AIBRAIN_ATTACHMENT_NOT_FOUND"
    assert provider.calls == []


def test_aibrain_endpoints_require_the_aibrain_permission(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        user = db.get(User, auth_context["user_id"])
        user.role = "reviewer"
        db.commit()

    response = TestClient(app).get(
        "/api/v1/aibrain/wallet",
        headers=auth_context["headers"],
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_aibrain_requests_forbid_extra_fields_and_invalid_topup_amounts(
    auth_context,
    auth_db,
) -> None:
    client = TestClient(app)

    extra = client.post(
        "/api/v1/aibrain/conversations",
        json={"title": "No extras", "model": "gpt-5.6-luna"},
        headers=auth_context["headers"],
    )
    invalid_topup = client.post(
        "/api/v1/aibrain/wallet/topup",
        json={"amount": 300},
        headers=auth_context["headers"],
    )

    assert extra.status_code == 422
    assert invalid_topup.status_code == 422
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        assert subscription.quota_credits_used == 0
        assert db.get(ReasoningWallet, auth_context["tenant_id"]) is None


def test_wallet_topup_rolls_back_when_primary_quota_is_insufficient(
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        subscription.quota_credits_total = 50
        db.commit()

    response = TestClient(app).post(
        "/api/v1/aibrain/wallet/topup",
        json={"amount": 100},
        headers=auth_context["headers"],
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TENANT_QUOTA_EXCEEDED"
    with auth_db() as db:
        assert db.get(ReasoningWallet, auth_context["tenant_id"]) is None
        assert db.scalar(select(ReasoningLedgerEntry.id)) is None
