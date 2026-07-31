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
    ChatConversation,
    ChatMessage,
    ReasoningLedgerEntry,
    ReasoningWallet,
    Subscription,
    Tenant,
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

    def __init__(self) -> None:
        self.presigned_keys: list[str] = []

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        assert expires_in > 0
        assert download_filename is None
        self.presigned_keys.append(key)
        return f"https://storage.example/{key}"


def _topup_payload(
    amount: int,
    *,
    idempotency_key: str | None = None,
) -> dict[str, object]:
    return {
        "amount": amount,
        "idempotency_key": idempotency_key or str(uuid4()),
    }


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


def test_delete_aibrain_conversation_soft_deletes_only_the_conversation(
    auth_context,
    auth_db,
) -> None:
    client = TestClient(app)
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={"title": "Delete only the conversation"},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    message_id = str(uuid4())
    with auth_db() as db:
        db.add(
            ChatMessage(
                id=message_id,
                tenant_id=auth_context["tenant_id"],
                conversation_id=conversation_id,
                role="user",
                content="Preserve this message",
                attachments=[],
                status="completed",
            )
        )
        db.commit()

    deleted = client.delete(
        f"/api/v1/aibrain/conversations/{conversation_id}",
        headers=auth_context["headers"],
    )
    listed = client.get(
        "/api/v1/aibrain/conversations",
        headers=auth_context["headers"],
    )
    detail = client.get(
        f"/api/v1/aibrain/conversations/{conversation_id}",
        headers=auth_context["headers"],
    )
    deleted_again = client.delete(
        f"/api/v1/aibrain/conversations/{conversation_id}",
        headers=auth_context["headers"],
    )

    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"deleted": True}
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 0
    assert detail.status_code == 404
    assert deleted_again.status_code == 404
    with auth_db() as db:
        conversation = db.get(ChatConversation, conversation_id)
        assert conversation is not None
        assert conversation.deleted_at is not None
        assert db.get(ChatMessage, message_id) is not None


def test_delete_aibrain_conversation_hides_cross_tenant_existence(
    auth_context,
    auth_db,
) -> None:
    other_tenant_id = "aibrain-delete-other-tenant"
    conversation_id = "aibrain-delete-foreign-conversation"
    with auth_db() as db:
        db.add(
            Tenant(
                id=other_tenant_id,
                slug="aibrain-delete-other",
                name="AIBrain Delete Other",
            )
        )
        db.flush()
        db.add(
            ChatConversation(
                id=conversation_id,
                tenant_id=other_tenant_id,
                title="Foreign conversation",
            )
        )
        db.commit()

    response = TestClient(app).delete(
        f"/api/v1/aibrain/conversations/{conversation_id}",
        headers=auth_context["headers"],
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AIBRAIN_CONVERSATION_NOT_FOUND"
    with auth_db() as db:
        assert db.get(ChatConversation, conversation_id).deleted_at is None


def test_clear_aibrain_conversations_preserves_messages_ledger_and_wallet_exactly(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    assert (
        client.post(
            "/api/v1/aibrain/wallet/topup",
            json=_topup_payload(500),
            headers=auth_context["headers"],
        ).status_code
        == 200
    )
    first_conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={"title": "Ledger-bearing conversation"},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    second_conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={"title": "Second conversation"},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    foreign_tenant_id = "aibrain-clear-foreign-tenant"
    foreign_conversation_id = "aibrain-clear-foreign-conversation"
    with auth_db() as db:
        db.add(
            Tenant(
                id=foreign_tenant_id,
                slug="aibrain-clear-foreign",
                name="AIBrain Clear Foreign",
            )
        )
        db.flush()
        db.add(
            ChatConversation(
                id=foreign_conversation_id,
                tenant_id=foreign_tenant_id,
                title="Foreign conversation must remain live",
            )
        )
        db.commit()
    provider = _FakeChatProvider(
        {
            "content": "A preserved assistant answer.",
            "model": "gpt-5.6-terra",
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)
    sent = client.post(
        f"/api/v1/aibrain/conversations/{first_conversation_id}/messages",
        json={"content": "Preserve the whole audit chain", "tier": "mid"},
        headers=auth_context["headers"],
    )
    assert sent.status_code == 200

    with auth_db() as db:
        messages_before = [
            (
                row.id,
                row.conversation_id,
                row.role,
                row.content,
                row.status,
                row.reserved_credits,
                row.charged_credits,
            )
            for row in db.scalars(
                select(ChatMessage).order_by(ChatMessage.id.asc())
            )
        ]
        ledger_before = [
            (
                row.id,
                row.chat_message_id,
                row.entry_type,
                row.amount_credits,
                row.available_delta,
                row.reserved_delta,
                row.available_after,
                row.reserved_after,
            )
            for row in db.scalars(
                select(ReasoningLedgerEntry).order_by(ReasoningLedgerEntry.id.asc())
            )
        ]
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        wallet_before = (
            wallet.available_credits,
            wallet.reserved_credits,
            wallet.total_topup_credits,
            wallet.total_spent_credits,
        )

    cleared = client.delete(
        "/api/v1/aibrain/conversations",
        headers=auth_context["headers"],
    )

    assert cleared.status_code == 200
    assert cleared.json()["data"] == {"deleted_count": 2}
    with auth_db() as db:
        conversations = [
            db.get(ChatConversation, first_conversation_id),
            db.get(ChatConversation, second_conversation_id),
        ]
        assert all(item is not None and item.deleted_at is not None for item in conversations)
        assert db.get(ChatConversation, foreign_conversation_id).deleted_at is None
        messages_after = [
            (
                row.id,
                row.conversation_id,
                row.role,
                row.content,
                row.status,
                row.reserved_credits,
                row.charged_credits,
            )
            for row in db.scalars(
                select(ChatMessage).order_by(ChatMessage.id.asc())
            )
        ]
        ledger_after = [
            (
                row.id,
                row.chat_message_id,
                row.entry_type,
                row.amount_credits,
                row.available_delta,
                row.reserved_delta,
                row.available_after,
                row.reserved_after,
            )
            for row in db.scalars(
                select(ReasoningLedgerEntry).order_by(ReasoningLedgerEntry.id.asc())
            )
        ]
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        wallet_after = (
            wallet.available_credits,
            wallet.reserved_credits,
            wallet.total_topup_credits,
            wallet.total_spent_credits,
        )

    assert messages_after == messages_before
    assert ledger_after == ledger_before
    assert wallet_after == wallet_before


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
        json=_topup_payload(500),
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


def test_reasoning_wallet_topup_replay_is_idempotent_before_primary_quota_debit(
    auth_context,
    auth_db,
) -> None:
    client = TestClient(app)
    payload = {
        "amount": 100,
        "idempotency_key": str(uuid4()),
    }

    first = client.post(
        "/api/v1/aibrain/wallet/topup",
        json=payload,
        headers=auth_context["headers"],
    )
    replay = client.post(
        "/api/v1/aibrain/wallet/topup",
        json=payload,
        headers=auth_context["headers"],
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["data"] == first.json()["data"]
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        topups = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"],
                    ReasoningLedgerEntry.entry_type == "topup",
                )
            )
        )
        assert subscription.quota_credits_used == 100
        assert wallet.available_credits == Decimal("100")
        assert wallet.total_topup_credits == Decimal("100")
        assert len(topups) == 1


def test_reasoning_wallet_topup_replay_returns_the_original_balance_snapshot(
    auth_context,
    auth_db,
) -> None:
    client = TestClient(app)
    idempotency_key = str(uuid4())
    first = client.post(
        "/api/v1/aibrain/wallet/topup",
        json=_topup_payload(100, idempotency_key=idempotency_key),
        headers=auth_context["headers"],
    )
    later = client.post(
        "/api/v1/aibrain/wallet/topup",
        json=_topup_payload(100),
        headers=auth_context["headers"],
    )
    replay = client.post(
        "/api/v1/aibrain/wallet/topup",
        json=_topup_payload(100, idempotency_key=idempotency_key),
        headers=auth_context["headers"],
    )

    assert first.status_code == 200
    assert first.json()["data"]["available_credits"] == 100
    assert later.json()["data"]["available_credits"] == 200
    assert replay.json()["data"] == first.json()["data"]
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
        )
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        topups = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"],
                    ReasoningLedgerEntry.entry_type == "topup",
                )
            )
        )
        assert subscription.quota_credits_used == 200
        assert wallet.available_credits == Decimal("200")
        assert len(topups) == 2


def test_message_reserves_then_settles_exact_token_usage(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    assert (
        client.post(
            "/api/v1/aibrain/wallet/topup",
            json=_topup_payload(500),
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


def test_message_provider_cost_prefers_authoritative_apimart_credits(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    assert (
        client.post(
            "/api/v1/aibrain/wallet/topup",
            json=_topup_payload(500),
            headers=auth_context["headers"],
        ).status_code
        == 200
    )
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FakeChatProvider(
        {
            "content": "Use the provider-reported cost.",
            "model": "gpt-5.6-luna",
            "prompt_tokens": 1_000,
            "completion_tokens": 500,
            "total_tokens": 1_500,
            "credits": Decimal("1.25"),
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Account for this answer", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    with auth_db() as db:
        usage = db.scalar(
            select(UsageRecord).where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.capability == "chat",
            )
        )
        assert usage.provider_cost_usd == Decimal("0.12500000")
        assert usage.cost_cents == 88


def test_message_provider_cost_uses_cache_read_and_write_rates(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        aibrain.settings,
        "engine_aibrain_low_input_provider_credits_per_m",
        Decimal("999"),
    )
    monkeypatch.setattr(
        aibrain.settings,
        "engine_aibrain_low_output_provider_credits_per_m",
        Decimal("999"),
    )
    client = TestClient(app)
    assert (
        client.post(
            "/api/v1/aibrain/wallet/topup",
            json=_topup_payload(500),
            headers=auth_context["headers"],
        ).status_code
        == 200
    )
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FakeChatProvider(
        {
            "content": "Use cache-specific rates.",
            "model": "gpt-5.6-luna",
            "prompt_tokens": 100_000,
            "completion_tokens": 50_000,
            "total_tokens": 150_000,
            "cached_prompt_tokens": 40_000,
            "cache_write_tokens": 10_000,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Account for cached context", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    with auth_db() as db:
        usage = db.scalar(
            select(UsageRecord).where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.capability == "chat",
            )
        )
        assert usage.provider_cost_usd == Decimal("0.29320000")
        assert usage.cost_cents == 205


def test_invalid_provider_cost_usage_releases_reservation_without_charge(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/aibrain/wallet/topup",
        json=_topup_payload(500),
        headers=auth_context["headers"],
    )
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FakeChatProvider(
        {
            "content": "Invalid provider usage must not leak the reservation.",
            "model": "gpt-5.6-luna",
            "prompt_tokens": 100,
            "completion_tokens": 0,
            "total_tokens": 100,
            "cached_prompt_tokens": 80,
            "cache_write_tokens": 30,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Reject invalid usage safely", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "AIBRAIN_PROVIDER_FAILED"
    with auth_db() as db:
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        usage = db.scalar(
            select(UsageRecord).where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.capability == "chat",
            )
        )
        assert wallet.available_credits == Decimal("500")
        assert wallet.reserved_credits == Decimal("0")
        assert usage.status == "released"
        assert usage.credits == Decimal("0")
        assert usage.cost_cents == 0


def test_message_provider_cost_uses_sol_high_tier_above_272k(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    assert (
        client.post(
            "/api/v1/aibrain/wallet/topup",
            json=_topup_payload(500),
            headers=auth_context["headers"],
        ).status_code
        == 200
    )
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FakeChatProvider(
        {
            "content": "Use the high-context Sol tier.",
            "model": "gpt-5.6-sol",
            "prompt_tokens": 272_001,
            "completion_tokens": 1_000,
            "total_tokens": 273_001,
            "cached_prompt_tokens": 0,
            "cache_write_tokens": 0,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Account for a long context", "tier": "high"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    with auth_db() as db:
        usage = db.scalar(
            select(UsageRecord).where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.capability == "chat",
            )
        )
        assert usage.provider_cost_usd == Decimal("2.21200800")
        assert usage.cost_cents == 1548


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
        json=_topup_payload(500),
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
        json=_topup_payload(500),
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


def test_empty_answer_failure_preserves_authoritative_provider_cost(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/aibrain/wallet/topup",
        json=_topup_payload(500),
        headers=auth_context["headers"],
    )
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FakeChatProvider(
        {
            "content": "",
            "model": "gpt-5.6-luna",
            "prompt_tokens": 1_000,
            "completion_tokens": 500,
            "total_tokens": 1_500,
            "credits": Decimal("1.25"),
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Return an empty answer", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 502
    with auth_db() as db:
        usage = db.scalar(
            select(UsageRecord).where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.capability == "chat",
            )
        )
        assert usage.status == "released"
        assert usage.credits == Decimal("0")
        assert usage.provider_cost_usd == Decimal("0.12500000")
        assert usage.cost_cents == 88


def test_missing_usage_releases_reservation_without_a_second_provider_call(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/aibrain/wallet/topup",
        json=_topup_payload(100),
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
        json=_topup_payload(100),
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
        json=_topup_payload(500),
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
        json=_topup_payload(100),
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
        history = client.get(
            f"/api/v1/aibrain/conversations/{conversation_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert response.status_code == 200
    expected_attachment = {
        "asset_id": image_id,
        "asset_type": "product_image",
        "mime_type": "image/png",
        "download_url": (
            f"https://storage.example/tenants/{auth_context['tenant_id']}/uploads/product.png"
        ),
    }
    assert response.json()["data"]["user_message"]["attachments"] == [expected_attachment]
    assert history.status_code == 200
    assert history.json()["data"]["messages"][0]["attachments"] == [expected_attachment]
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
        json=_topup_payload(100),
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


def test_conversation_detail_filters_a_foreign_tenant_message_with_a_corrupt_link(
    auth_context,
    auth_db,
) -> None:
    client = TestClient(app)
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={"title": "Tenant-safe messages"},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    foreign_tenant_id = str(uuid4())
    with auth_db() as db:
        db.add(
            Tenant(
                id=foreign_tenant_id,
                slug=f"aibrain-foreign-{uuid4().hex[:8]}",
                name="Foreign AIBRAIN Tenant",
            )
        )
        db.flush()
        db.add(
            ChatMessage(
                tenant_id=foreign_tenant_id,
                conversation_id=conversation_id,
                role="assistant",
                content="must not leak",
                attachments=[],
                tier="low",
                model="gpt-5.6-luna",
                status="completed",
            )
        )
        db.commit()

    detail = client.get(
        f"/api/v1/aibrain/conversations/{conversation_id}",
        headers=auth_context["headers"],
    )

    assert detail.status_code == 200
    assert detail.json()["data"]["messages"] == []


def test_conversation_detail_never_presigns_a_foreign_attachment_from_corrupt_data(
    auth_context,
    auth_db,
) -> None:
    client = TestClient(app)
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={"title": "Tenant-safe attachment URLs"},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    foreign_tenant_id = str(uuid4())
    with auth_db() as db:
        db.add(
            Tenant(
                id=foreign_tenant_id,
                slug=f"aibrain-foreign-asset-{uuid4().hex[:8]}",
                name="Foreign AIBRAIN Asset Tenant",
            )
        )
        db.flush()
        foreign_asset = Asset(
            tenant_id=foreign_tenant_id,
            type="product_image",
            source="upload",
            storage_key=f"tenants/{foreign_tenant_id}/uploads/private.png",
            mime_type="image/png",
            status="ready",
        )
        db.add(foreign_asset)
        db.flush()
        foreign_asset_id = foreign_asset.id
        db.add(
            ChatMessage(
                tenant_id=auth_context["tenant_id"],
                conversation_id=conversation_id,
                role="user",
                content="corrupt foreign attachment link",
                attachments=[
                    {
                        "asset_id": foreign_asset_id,
                        "asset_type": foreign_asset.type,
                        "mime_type": foreign_asset.mime_type,
                    }
                ],
                tier="low",
                model="gpt-5.6-luna",
                status="completed",
            )
        )
        db.commit()

    storage = _FakeObjectStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        detail = client.get(
            f"/api/v1/aibrain/conversations/{conversation_id}",
            headers=auth_context["headers"],
        )
    finally:
        app.dependency_overrides.pop(get_object_storage, None)

    assert detail.status_code == 200
    assert detail.json()["data"]["messages"][0]["attachments"] == [
        {
            "asset_id": foreign_asset_id,
            "asset_type": "product_image",
            "mime_type": "image/png",
            "download_url": None,
        }
    ]
    assert storage.presigned_keys == []


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
        json=_topup_payload(300),
        headers=auth_context["headers"],
    )
    missing_idempotency_key = client.post(
        "/api/v1/aibrain/wallet/topup",
        json={"amount": 100},
        headers=auth_context["headers"],
    )
    malformed_idempotency_key = client.post(
        "/api/v1/aibrain/wallet/topup",
        json={"amount": 100, "idempotency_key": "not-a-uuid"},
        headers=auth_context["headers"],
    )

    assert extra.status_code == 422
    assert invalid_topup.status_code == 422
    assert missing_idempotency_key.status_code == 422
    assert malformed_idempotency_key.status_code == 422
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
        json=_topup_payload(100),
        headers=auth_context["headers"],
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TENANT_QUOTA_EXCEEDED"
    with auth_db() as db:
        assert db.get(ReasoningWallet, auth_context["tenant_id"]) is None
        assert db.scalar(select(ReasoningLedgerEntry.id)) is None
