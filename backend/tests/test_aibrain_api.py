import asyncio
import threading
import time
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
        self.task: asyncio.Task[object] | None = None
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    async def chat(self, payload: dict) -> dict:
        self.calls.append(payload)
        self.task = asyncio.current_task()
        return await asyncio.to_thread(self._chat_sync)

    def _chat_sync(self) -> dict:
        self.started.set()
        try:
            if not self.release.wait(timeout=5):
                raise TimeoutError("Cancelled provider thread was not released.")
            return {
                "content": "thread result is lost after task cancellation",
                "model": "gpt-5.6-luna",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            }
        finally:
            self.finished.set()


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


def test_inflight_heartbeat_scheduler_bounds_workers_and_retries_full_queue(
    monkeypatch,
) -> None:
    scheduler = aibrain._InflightHeartbeatScheduler(worker_count=2, queue_capacity=1)
    touch_state = threading.Condition()
    release_touches = threading.Event()
    calls: list[str] = []
    active_touches = 0
    max_active_touches = 0

    def blocking_touch(_bind, *, tenant_id: str, message_id: str) -> bool:
        nonlocal active_touches, max_active_touches
        assert tenant_id == "bounded-tenant"
        with touch_state:
            calls.append(message_id)
            active_touches += 1
            max_active_touches = max(max_active_touches, active_touches)
            touch_state.notify_all()
        try:
            assert release_touches.wait(timeout=5)
        finally:
            with touch_state:
                active_touches -= 1
                touch_state.notify_all()
        return False

    monkeypatch.setattr(aibrain, "_touch_pending_chat_message", blocking_touch)
    message_ids = [f"message-{index}" for index in range(8)]
    threads: list[threading.Thread] = []
    try:
        for message_id in message_ids:
            scheduler.register(
                object(),
                tenant_id="bounded-tenant",
                message_id=message_id,
                interval_seconds=0.01,
            )

        deadline = time.monotonic() + 5
        with touch_state:
            while max_active_touches < 2:
                remaining = deadline - time.monotonic()
                assert remaining > 0
                touch_state.wait(timeout=remaining)

        assert scheduler._scheduler_thread is not None
        threads = [scheduler._scheduler_thread, *scheduler._workers]
        assert len(threads) == 3
        assert all(thread.daemon for thread in threads)
        assert max_active_touches == 2
        assert scheduler._work_queue.qsize() <= 1

        release_touches.set()
        deadline = time.monotonic() + 5
        with scheduler._condition:
            while scheduler._registry:
                remaining = deadline - time.monotonic()
                assert remaining > 0
                scheduler._condition.wait(timeout=remaining)

        assert sorted(calls) == message_ids
        assert max_active_touches == 2
        assert [scheduler._scheduler_thread, *scheduler._workers] == threads
        assert scheduler._work_queue.empty()
    finally:
        release_touches.set()
        scheduler.shutdown(timeout=2)

    assert all(not thread.is_alive() for thread in threads)


def test_inflight_heartbeat_scheduler_retries_transient_touch_failure(
    monkeypatch,
) -> None:
    scheduler = aibrain._InflightHeartbeatScheduler(worker_count=1, queue_capacity=1)
    completed = threading.Event()
    calls = 0

    def flaky_touch(_bind, *, tenant_id: str, message_id: str) -> bool:
        nonlocal calls
        assert (tenant_id, message_id) == ("retry-tenant", "retry-message")
        calls += 1
        if calls == 1:
            raise RuntimeError("transient heartbeat failure")
        completed.set()
        return False

    monkeypatch.setattr(aibrain, "_touch_pending_chat_message", flaky_touch)
    try:
        scheduler.register(
            object(),
            tenant_id="retry-tenant",
            message_id="retry-message",
            interval_seconds=0.01,
        )
        assert completed.wait(timeout=5)
        deadline = time.monotonic() + 5
        with scheduler._condition:
            while scheduler._registry:
                remaining = deadline - time.monotonic()
                assert remaining > 0
                scheduler._condition.wait(timeout=remaining)
        assert calls == 2
    finally:
        scheduler.shutdown(timeout=2)


def test_inflight_message_heartbeat_stop_is_idempotent_during_touch(
    monkeypatch,
) -> None:
    scheduler = aibrain._InflightHeartbeatScheduler(worker_count=1, queue_capacity=1)
    target_started = threading.Event()
    release_target = threading.Event()
    target_finished = threading.Event()
    sentinel_finished = threading.Event()
    calls: list[str] = []

    def racing_touch(_bind, *, tenant_id: str, message_id: str) -> bool:
        assert tenant_id == "race-tenant"
        calls.append(message_id)
        if message_id == "target-message":
            target_started.set()
            assert release_target.wait(timeout=5)
            target_finished.set()
            return True
        assert message_id == "sentinel-message"
        sentinel_finished.set()
        return False

    monkeypatch.setattr(aibrain, "_INFLIGHT_HEARTBEAT_SCHEDULER", scheduler)
    monkeypatch.setattr(aibrain, "_inflight_heartbeat_interval_seconds", lambda: 0.01)
    monkeypatch.setattr(aibrain, "_touch_pending_chat_message", racing_touch)
    heartbeat = aibrain.InflightMessageHeartbeat(
        object(),
        tenant_id="race-tenant",
        message_id="target-message",
    )
    try:
        heartbeat.start()
        assert target_started.wait(timeout=5)
        heartbeat.stop()
        heartbeat.stop()
        with scheduler._condition:
            assert ("race-tenant", "target-message") not in scheduler._registry

        release_target.set()
        assert target_finished.wait(timeout=5)
        scheduler.register(
            object(),
            tenant_id="race-tenant",
            message_id="sentinel-message",
            interval_seconds=0.01,
        )
        assert sentinel_finished.wait(timeout=5)
        deadline = time.monotonic() + 5
        with scheduler._condition:
            while scheduler._registry:
                remaining = deadline - time.monotonic()
                assert remaining > 0
                scheduler._condition.wait(timeout=remaining)

        assert calls.count("target-message") == 1
        assert calls.count("sentinel-message") == 1
    finally:
        release_target.set()
        heartbeat.stop()
        scheduler.shutdown(timeout=2)


@pytest.mark.parametrize(
    ("messages", "expected_tokens"),
    [
        ([{"role": "user", "content": "你好"}], 38),
        (
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "é"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://storage.example/image.png"},
                        },
                    ],
                }
            ],
            8_226,
        ),
    ],
)
def test_prompt_token_upper_bound_counts_utf8_bytes_framing_and_image_budget(
    messages: list[dict[str, object]],
    expected_tokens: int,
) -> None:
    assert aibrain._prompt_token_upper_bound(messages) == expected_tokens


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
    assert data["assistant_message"]["charged_credits"] == 11.2
    assert data["wallet"]["available_credits"] == 488.8
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
        assert wallet.available_credits == Decimal("488.800000")
        assert wallet.reserved_credits == Decimal("0")
        assert [entry.entry_type for entry in entries] == ["topup", "reserve", "settle"]
        assert Decimal("11.200000") <= entries[1].amount_credits < Decimal("200")
        assert entries[2].amount_credits == Decimal("11.200000")
        assert usage.credits == Decimal("11.200000")
        assert usage.provider_cost_usd == Decimal("0.00800000")
        assert usage.chat_message_id == assistant.id
        assert assistant.input_rate == Decimal("2.800000")
        assert assistant.output_rate == Decimal("16.800000")
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
            "completion_tokens": 4_000,
            "total_tokens": 104_000,
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
        assert usage.provider_cost_usd == Decimal("0.07240000")
        assert usage.cost_cents == 51


@pytest.mark.parametrize(
    "provider_metadata",
    [
        {"cached_prompt_tokens": 80, "cache_write_tokens": 30},
        {"credits": "NaN"},
        {"credits": "Infinity"},
        {"credits": "100000000000"},
    ],
)
def test_invalid_provider_cost_usage_opens_tenant_cooldown_without_charge(
    auth_context,
    auth_db,
    monkeypatch,
    provider_metadata: dict[str, object],
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
            **provider_metadata,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Reject invalid usage safely", "tier": "low"},
        headers=auth_context["headers"],
    )
    replay_response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Do not replay invalid billing metadata", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "AIBRAIN_PROVIDER_USAGE_INVALID"
    assert replay_response.status_code == 503
    assert replay_response.json()["error"]["code"] == (
        "AIBRAIN_PROVIDER_USAGE_ANOMALY_COOLDOWN"
    )
    assert len(provider.calls) == 1
    with auth_db() as db:
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        messages = list(
            db.scalars(
                select(ChatMessage).where(
                    ChatMessage.tenant_id == auth_context["tenant_id"]
                )
            )
        )
        usage = db.scalar(
            select(UsageRecord).where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.capability == "chat",
            )
        )
        assert wallet.available_credits == Decimal("500")
        assert wallet.reserved_credits == Decimal("0")
        assert wallet.total_spent_credits == Decimal("0")
        assert len(messages) == 1
        assert messages[0].status == "failed"
        assert messages[0].error_code == "AIBRAIN_PROVIDER_USAGE_INVALID"
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
            json=_topup_payload(2000),
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
    error = response.json()["error"]
    assert error["code"] == "AIBRAIN_INSUFFICIENT_BALANCE"
    assert set(error["detail"]) == {
        "required_credits",
        "available_credits",
        "shortfall_credits",
        "temporary_reservation",
    }
    assert error["detail"]["available_credits"] == 0.0
    assert error["detail"]["required_credits"] > 0
    assert error["detail"]["shortfall_credits"] == error["detail"]["required_credits"]
    assert error["detail"]["temporary_reservation"] is True
    assert provider.calls == []
    with auth_db() as db:
        assert db.get(ReasoningWallet, auth_context["tenant_id"]) is None
        assert db.scalar(select(ReasoningLedgerEntry.id)) is None
        assert db.scalar(select(ChatMessage.id)) is None


def test_prompt_hard_limit_rejects_before_provider_or_reservation(
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
    monkeypatch.setattr(
        aibrain.settings,
        "engine_aibrain_max_prompt_tokens",
        256,
    )

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "x" * 20_000, "tier": "low"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "AIBRAIN_PROMPT_LIMIT_EXCEEDED"
    assert error["message"] == "AIBRAIN prompt exceeds the local safety limit."
    assert error["detail"]["prompt_token_upper_bound"] > 256
    assert error["detail"]["max_prompt_tokens"] == 256
    assert error["details"] is None
    assert provider.calls == []
    with auth_db() as db:
        assert db.get(ReasoningWallet, auth_context["tenant_id"]) is None
        assert db.scalar(select(ReasoningLedgerEntry.id)) is None
        assert db.scalar(select(ChatMessage.id)) is None


@pytest.mark.parametrize(
    (
        "prompt_tokens",
        "completion_tokens",
        "expected_charge",
        "expected_provider_cost_usd",
    ),
    [
        (922_001, 1, Decimal("1032.646720"), Decimal("1.47520880")),
        (1, 4_097, Decimal("27.526240"), Decimal("0.01966640")),
    ],
)
def test_provider_usage_above_pre_authorized_envelope_delivers_with_capped_charge(
    auth_context,
    auth_db,
    monkeypatch,
    prompt_tokens: int,
    completion_tokens: int,
    expected_charge: Decimal,
    expected_provider_cost_usd: Decimal,
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
    provider_result = {
        "content": "provider answer remains deliverable",
        "model": "gpt-5.6-luna",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    provider = _FakeChatProvider(provider_result)
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Keep provider usage inside the authorization", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    assert response.json()["data"]["assistant_message"]["content"] == (
        "provider answer remains deliverable"
    )
    assert len(provider.calls) == 1
    with auth_db() as db:
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        user_message = db.scalar(
            select(ChatMessage).where(
                ChatMessage.tenant_id == auth_context["tenant_id"],
                ChatMessage.role == "user",
            )
        )
        assistant_message = db.scalar(
            select(ChatMessage).where(
                ChatMessage.tenant_id == auth_context["tenant_id"],
                ChatMessage.role == "assistant",
            )
        )
        usage = db.scalar(
            select(UsageRecord).where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.capability == "chat",
            )
        )
        ledger_entries = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"]
                )
            )
        )
        settle_entry = next(
            entry for entry in ledger_entries if entry.entry_type == "settle"
        )
        assert wallet.available_credits == Decimal("500") - expected_charge
        assert wallet.reserved_credits == Decimal("0")
        assert wallet.total_spent_credits == expected_charge
        assert user_message.status == "completed"
        assert user_message.error_code is None
        assert assistant_message.status == "completed"
        assert assistant_message.prompt_tokens == prompt_tokens
        assert assistant_message.completion_tokens == completion_tokens
        assert assistant_message.total_tokens == prompt_tokens + completion_tokens
        assert assistant_message.charged_credits == expected_charge
        assert assistant_message.provider_cost_usd == expected_provider_cost_usd
        assert [entry.entry_type for entry in ledger_entries] == [
            "topup",
            "reserve",
            "settle",
        ]
        assert settle_entry.details["billable_prompt_tokens"] == min(prompt_tokens, 922_000)
        assert settle_entry.details["billable_completion_tokens"] == min(
            completion_tokens,
            4_096,
        )
        assert settle_entry.details["reported_prompt_tokens"] == prompt_tokens
        assert settle_entry.details["reported_completion_tokens"] == completion_tokens
        assert usage.status == "settled"
        assert usage.quantity == Decimal(prompt_tokens + completion_tokens)
        assert usage.credits == expected_charge
        assert usage.provider_cost_usd == expected_provider_cost_usd


@pytest.mark.parametrize(
    ("prompt_tokens", "completion_tokens", "total_tokens", "usage_contract_valid"),
    [
        (1, 1, 3, None),
        (1, 1, 2, False),
        (999_999_999, 1, 1_000_000_000, None),
    ],
)
def test_malformed_provider_usage_opens_tenant_cooldown_before_replay(
    auth_context,
    auth_db,
    monkeypatch,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    usage_contract_valid: bool | None,
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
    provider_result = {
        "content": "provider usage is malformed",
        "model": "gpt-5.6-luna",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    if usage_contract_valid is not None:
        provider_result["_usage_contract_valid"] = usage_contract_valid
    provider = _FakeChatProvider(provider_result)
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)
    url = f"/api/v1/aibrain/conversations/{conversation_id}/messages"

    first_response = client.post(
        url,
        json={"content": "Trigger malformed usage", "tier": "low"},
        headers=auth_context["headers"],
    )
    replay_conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    second_response = client.post(
        f"/api/v1/aibrain/conversations/{replay_conversation_id}/messages",
        json={"content": "Do not replay paid work", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert first_response.status_code == 502
    first_error = first_response.json()["error"]
    assert first_error["code"] == "AIBRAIN_PROVIDER_USAGE_INVALID"
    assert first_error["detail"] is None
    assert second_response.status_code == 503
    assert second_response.json()["error"]["code"] == (
        "AIBRAIN_PROVIDER_USAGE_ANOMALY_COOLDOWN"
    )
    assert len(provider.calls) == 1
    with auth_db() as db:
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        messages = list(
            db.scalars(
                select(ChatMessage).where(
                    ChatMessage.tenant_id == auth_context["tenant_id"]
                )
            )
        )
        ledger_entries = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"]
                )
            )
        )
        usage_records = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == auth_context["tenant_id"],
                    UsageRecord.capability == "chat",
                )
            )
        )
        assert wallet.available_credits == Decimal("500")
        assert wallet.reserved_credits == Decimal("0")
        assert wallet.total_spent_credits == Decimal("0")
        assert len(messages) == 1
        assert messages[0].status == "failed"
        assert messages[0].error_code == "AIBRAIN_PROVIDER_USAGE_INVALID"
        assert [entry.entry_type for entry in ledger_entries] == [
            "topup",
            "reserve",
            "release",
        ]
        assert len(usage_records) == 1
        assert usage_records[0].status == "released"
        assert usage_records[0].credits == Decimal("0")
        assert usage_records[0].provider_cost_usd > 0


def test_provider_usage_at_configured_hard_bound_settles_normally(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/aibrain/wallet/topup",
        json=_topup_payload(2000),
        headers=auth_context["headers"],
    )
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FakeChatProvider(
        {
            "content": "usage exactly at the configured safety envelope",
            "model": "gpt-5.6-luna",
            "prompt_tokens": 922_000,
            "completion_tokens": 4_096,
            "total_tokens": 926_096,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Accept the exact provider boundary", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    assert response.json()["data"]["assistant_message"]["charged_credits"] == 1060.16512
    assert len(provider.calls) == 1
    with auth_db() as db:
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        usage = db.scalar(
            select(UsageRecord).where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.capability == "chat",
            )
        )
        assert wallet.available_credits == Decimal("939.834880")
        assert wallet.reserved_credits == Decimal("0")
        assert wallet.total_spent_credits == Decimal("1060.165120")
        assert usage.status == "settled"
        assert usage.quantity == Decimal("926096")


def test_inflight_exposure_limit_returns_structured_402_before_provider(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
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
    target_conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={"title": "Exposure target"},
        headers=auth_context["headers"],
    ).json()["data"]["id"]

    with auth_db() as db:
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("1000"),
            operation_key=f"exposure-topup:{auth_context['tenant_id']}",
        )
        for index in range(2):
            conversation = ChatConversation(
                tenant_id=auth_context["tenant_id"],
                title=f"Legacy in-flight {index}",
            )
            db.add(conversation)
            db.flush([conversation])
            message = ChatMessage(
                tenant_id=auth_context["tenant_id"],
                conversation_id=conversation.id,
                role="user",
                content="A legacy request already sent to the provider",
                attachments=[],
                tier="high",
                model="gpt-5.6-sol",
                status="pending",
            )
            db.add(message)
            db.flush([message])
            reservation = aibrain._apply_reasoning_wallet_change(
                db,
                tenant_id=auth_context["tenant_id"],
                entry_type="reserve",
                amount_credits=Decimal("1"),
                chat_message_id=message.id,
                operation_key=f"reserve:{message.id}",
                details={"legacy_without_exposure_metadata": True},
            ).ledger_entry.amount_credits
            message.reserved_credits = reservation
        db.commit()

    response = client.post(
        f"/api/v1/aibrain/conversations/{target_conversation_id}/messages",
        json={"content": "Do not start a third exposed request", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 402
    error = response.json()["error"]
    assert error["code"] == "AIBRAIN_INFLIGHT_EXPOSURE_LIMIT"
    assert error["message"] == (
        "Too much AIBRAIN work is already in progress. "
        "Wait for an existing request to finish before retrying."
    )
    assert set(error["detail"]) == {
        "in_flight_exposure_credits",
        "requested_exposure_credits",
        "exposure_limit_credits",
        "excess_credits",
        "in_flight_request_count",
        "retryable",
    }
    assert error["detail"]["in_flight_exposure_credits"] == 10_601.6512
    assert error["detail"]["requested_exposure_credits"] == 5_300.8256
    assert error["detail"]["exposure_limit_credits"] == 10_601.6512
    assert error["detail"]["excess_credits"] == error["detail"][
        "requested_exposure_credits"
    ]
    assert error["detail"]["in_flight_request_count"] == 2
    assert error["detail"]["retryable"] is True
    assert error["details"] is None
    assert provider.calls == []
    with auth_db() as db:
        messages = list(
            db.scalars(
                select(ChatMessage).where(
                    ChatMessage.tenant_id == auth_context["tenant_id"],
                    ChatMessage.role == "user",
                )
            )
        )
        reserves = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"],
                    ReasoningLedgerEntry.entry_type == "reserve",
                )
            )
        )
        assert len(messages) == 2
        assert len(reserves) == 2


def test_dynamic_reservation_can_exceed_200_and_settles_exact_usage(
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
            "content": "An answer whose estimated prompt requires more than 200 credits.",
            "model": "gpt-5.6-sol",
            "prompt_tokens": 20_000,
            "completion_tokens": 4_096,
            "total_tokens": 24_096,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(aibrain, "_estimate_prompt_tokens", lambda _messages: 20_000)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Reserve the full dynamic amount", "tier": "high"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["assistant_message"]["charged_credits"] == 249.6256
    assert data["assistant_message"]["reserved_credits"] > 200.0
    assert data["wallet"]["available_credits"] == 250.3744
    assert data["wallet"]["reserved_credits"] == 0.0
    assert provider.calls[0]["max_completion_tokens"] == 4_096
    with auth_db() as db:
        entries = list(
            db.scalars(
                select(ReasoningLedgerEntry)
                .where(ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"])
                .order_by(ReasoningLedgerEntry.created_at.asc())
            )
        )
        reservation = next(entry for entry in entries if entry.entry_type == "reserve")
        settlement = next(entry for entry in entries if entry.entry_type == "settle")
        assert reservation.amount_credits > Decimal("200")
        assert settlement.amount_credits == Decimal("249.625600")


def test_dynamic_reservation_rejects_before_provider_when_full_budget_is_unfunded(
    auth_context,
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
            "content": "must not be returned",
            "model": "gpt-5.6-sol",
            "prompt_tokens": 10,
            "completion_tokens": 10,
            "total_tokens": 20,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "A short high-tier request", "tier": "high"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 402
    assert response.json()["error"]["code"] == "AIBRAIN_INSUFFICIENT_BALANCE"
    assert provider.calls == []


def test_actual_usage_above_estimate_expands_reservation_without_truncating(
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
            "content": "Settle the actual amount.",
            "model": "gpt-5.6-sol",
            "prompt_tokens": 20_000,
            "completion_tokens": 4_096,
            "total_tokens": 24_096,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(aibrain, "_estimate_prompt_tokens", lambda _messages: 1)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Force an underestimated prompt", "tier": "high"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    assistant = response.json()["data"]["assistant_message"]
    assert assistant["charged_credits"] == 249.6256
    assert assistant["reserved_credits"] == 249.6256
    with auth_db() as db:
        reserves = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"],
                    ReasoningLedgerEntry.entry_type == "reserve",
                )
            )
        )
        assert len(reserves) == 2
        assert sum(entry.amount_credits for entry in reserves) == Decimal("249.625600")


def test_actual_usage_shortfall_delivers_answer_and_settles_actual_charge(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    with auth_db() as db:
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("200"),
            operation_key=f"seed-topup:{auth_context['tenant_id']}",
        )
        db.commit()
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FakeChatProvider(
        {
            "content": "Deliver the completed answer and settle its actual charge.",
            "model": "gpt-5.6-sol",
            "prompt_tokens": 20_000,
            "completion_tokens": 4_096,
            "total_tokens": 24_096,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(aibrain, "_estimate_prompt_tokens", lambda _messages: 1)

    response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Force an unfunded estimate", "tier": "high"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["assistant_message"]["content"] == (
        "Deliver the completed answer and settle its actual charge."
    )
    assert data["assistant_message"]["charged_credits"] == 249.6256
    assert len(provider.calls) == 1
    with auth_db() as db:
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        assistant = db.scalar(
            select(ChatMessage).where(
                ChatMessage.tenant_id == auth_context["tenant_id"],
                ChatMessage.role == "assistant",
            )
        )
        entries = list(
            db.scalars(
                select(ReasoningLedgerEntry)
                .where(ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"])
                .order_by(ReasoningLedgerEntry.created_at.asc())
            )
        )
        usage = db.scalar(select(UsageRecord).where(UsageRecord.capability == "chat"))
        assert assistant is not None
        assert assistant.status == "completed"
        assert assistant.charged_credits == Decimal("249.625600")
        assert wallet.available_credits == Decimal("-49.625600")
        assert wallet.reserved_credits == Decimal("0")
        assert wallet.total_spent_credits == Decimal("249.625600")
        assert [entry.entry_type for entry in entries] == ["topup", "reserve", "settle"]
        assert entries[-1].details["reservation_shortfall"] == "111.988800"
        assert entries[-1].details["overdraft_after"] == "49.625600"
        assert usage.status == "settled"
        assert usage.credits == Decimal("249.625600")


def test_outstanding_reasoning_balance_blocks_new_provider_work(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    with auth_db() as db:
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("200"),
            operation_key=f"seed-topup:{auth_context['tenant_id']}",
        )
        db.commit()
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FakeChatProvider(
        {
            "content": "The first completed answer creates an outstanding balance.",
            "model": "gpt-5.6-sol",
            "prompt_tokens": 20_000,
            "completion_tokens": 4_096,
            "total_tokens": 24_096,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(aibrain, "_estimate_prompt_tokens", lambda _messages: 1)

    completed = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Create a settled overdraft", "tier": "high"},
        headers=auth_context["headers"],
    )
    blocked = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Do not call the provider again", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert completed.status_code == 200
    assert blocked.status_code == 402
    assert blocked.json()["error"] == {
        "code": "AIBRAIN_OUTSTANDING_BALANCE",
        "message": "Pay the outstanding AIBRAIN balance before starting new work.",
        "request_id": blocked.headers["X-Request-ID"],
        "detail": {
            "available_credits": -49.6256,
            "outstanding_credits": 49.6256,
        },
        "details": None,
    }
    assert len(provider.calls) == 1
    with auth_db() as db:
        messages = list(
            db.scalars(
                select(ChatMessage).where(
                    ChatMessage.tenant_id == auth_context["tenant_id"]
                )
            )
        )
        entries = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"]
                )
            )
        )
        usages = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == auth_context["tenant_id"]
                )
            )
        )
        assert len(messages) == 2
        assert len(entries) == 3
        assert len(usages) == 1


def test_topups_automatically_restore_aibrain_access_after_debt_is_repaid(
    auth_context,
    auth_db,
    monkeypatch,
) -> None:
    client = TestClient(app)
    with auth_db() as db:
        aibrain._apply_reasoning_wallet_change(
            db,
            tenant_id=auth_context["tenant_id"],
            entry_type="topup",
            amount_credits=Decimal("200"),
            operation_key=f"seed-topup:{auth_context['tenant_id']}",
        )
        db.commit()
    conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FakeChatProvider(
        {
            "content": "Settle a debt larger than one 100-credit top-up.",
            "model": "gpt-5.6-sol",
            "prompt_tokens": 40_000,
            "completion_tokens": 4_096,
            "total_tokens": 44_096,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(aibrain, "_estimate_prompt_tokens", lambda _messages: 1)

    overdrawn = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Create a larger settled overdraft", "tier": "high"},
        headers=auth_context["headers"],
    )
    still_negative = client.post(
        "/api/v1/aibrain/wallet/topup",
        json=_topup_payload(100),
        headers=auth_context["headers"],
    )
    still_blocked = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Remain blocked while debt exists", "tier": "low"},
        headers=auth_context["headers"],
    )
    repaid = client.post(
        "/api/v1/aibrain/wallet/topup",
        json=_topup_payload(100),
        headers=auth_context["headers"],
    )
    provider.result = {
        "content": "Access resumed automatically after repayment.",
        "model": "gpt-5.6-luna",
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
    }
    resumed = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Resume without manual intervention", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert overdrawn.status_code == 200
    assert still_negative.status_code == 200
    assert still_negative.json()["data"]["available_credits"] == -61.6256
    assert still_blocked.status_code == 402
    assert still_blocked.json()["error"]["detail"]["outstanding_credits"] == 61.6256
    assert repaid.status_code == 200
    assert repaid.json()["data"]["available_credits"] == 38.3744
    assert resumed.status_code == 200
    assert resumed.json()["data"]["assistant_message"]["content"] == (
        "Access resumed automatically after repayment."
    )
    assert len(provider.calls) == 2


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
    replay_response = client.post(
        f"/api/v1/aibrain/conversations/{conversation_id}/messages",
        json={"content": "Do not replay missing usage", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 502
    first_error = response.json()["error"]
    assert first_error["code"] == "AIBRAIN_USAGE_MISSING"
    assert first_error["detail"] is None
    assert replay_response.status_code == 503
    assert replay_response.json()["error"]["code"] == (
        "AIBRAIN_PROVIDER_USAGE_ANOMALY_COOLDOWN"
    )
    assert len(provider.calls) == 1
    with auth_db() as db:
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        messages = list(
            db.scalars(
                select(ChatMessage).where(
                    ChatMessage.tenant_id == auth_context["tenant_id"]
                )
            )
        )
        ledger_entries = list(
            db.scalars(
                select(ReasoningLedgerEntry).where(
                    ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"]
                )
            )
        )
        usage_records = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == auth_context["tenant_id"],
                    UsageRecord.capability == "chat",
                )
            )
        )
        assert wallet.available_credits == Decimal("100")
        assert wallet.reserved_credits == Decimal("0")
        assert wallet.total_spent_credits == Decimal("0")
        assert len(messages) == 1
        assert messages[0].status == "failed"
        assert messages[0].error_code == "AIBRAIN_USAGE_MISSING"
        assert [entry.entry_type for entry in ledger_entries] == [
            "topup",
            "reserve",
            "release",
        ]
        assert len(usage_records) == 1
        assert usage_records[0].status == "released"
        assert usage_records[0].credits == Decimal("0")
        assert usage_records[0].provider_cost_usd > 0


def test_provider_usage_anomaly_cooldown_expires_and_allows_a_later_attempt(
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
            "content": "Missing usage first",
            "model": "gpt-5.6-luna",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)
    url = f"/api/v1/aibrain/conversations/{conversation_id}/messages"

    first_response = client.post(
        url,
        json={"content": "Trigger a temporary usage anomaly", "tier": "low"},
        headers=auth_context["headers"],
    )
    assert first_response.status_code == 502
    with auth_db() as db:
        failed_message = db.scalar(
            select(ChatMessage).where(
                ChatMessage.tenant_id == auth_context["tenant_id"],
                ChatMessage.error_code == "AIBRAIN_USAGE_MISSING",
            )
        )
        failed_message.updated_at = datetime.now(UTC) - timedelta(
            seconds=aibrain.settings.engine_aibrain_usage_anomaly_cooldown_seconds + 1
        )
        db.commit()

    provider.result = {
        "content": "Usage recovered after cooldown",
        "model": "gpt-5.6-luna",
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
    }
    retry_response = client.post(
        url,
        json={"content": "Retry after the cooldown", "tier": "low"},
        headers=auth_context["headers"],
    )

    assert retry_response.status_code == 200
    assert retry_response.json()["data"]["assistant_message"]["content"] == (
        "Usage recovered after cooldown"
    )
    assert len(provider.calls) == 2


def test_provider_usage_anomaly_cooldown_is_tenant_scoped(
    auth_context,
    monkeypatch,
) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/aibrain/wallet/topup",
        json=_topup_payload(100),
        headers=auth_context["headers"],
    )
    first_conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=auth_context["headers"],
    ).json()["data"]["id"]
    provider = _FakeChatProvider(
        {
            "content": "Tenant A missing usage",
            "model": "gpt-5.6-luna",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )
    monkeypatch.setattr(aibrain, "resolve", lambda *_args, **_kwargs: provider)

    first_response = client.post(
        f"/api/v1/aibrain/conversations/{first_conversation_id}/messages",
        json={"content": "Open tenant A cooldown", "tier": "low"},
        headers=auth_context["headers"],
    )
    assert first_response.status_code == 502

    suffix = uuid4().hex[:8]
    registration = client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": f"cooldown-{suffix}",
            "tenant_name": "Cooldown Isolation",
            "email": f"cooldown-{suffix}@example.com",
            "password": "secret-pass",
        },
    )
    assert registration.status_code == 201
    second_token = registration.json()["data"]["token"]["access_token"]
    second_headers = {"Authorization": f"Bearer {second_token}"}
    client.post(
        "/api/v1/aibrain/wallet/topup",
        json=_topup_payload(100),
        headers=second_headers,
    )
    second_conversation_id = client.post(
        "/api/v1/aibrain/conversations",
        json={},
        headers=second_headers,
    ).json()["data"]["id"]
    provider.result = {
        "content": "Tenant B remains available",
        "model": "gpt-5.6-luna",
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
    }

    second_response = client.post(
        f"/api/v1/aibrain/conversations/{second_conversation_id}/messages",
        json={"content": "Tenant B is isolated", "tier": "low"},
        headers=second_headers,
    )

    assert second_response.status_code == 200
    assert second_response.json()["data"]["assistant_message"]["content"] == (
        "Tenant B remains available"
    )
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_cancelled_provider_task_preserves_exposure_until_stale_recovery(
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
        send_task = asyncio.create_task(
            aibrain.send_chat_message(
                db,
                user=user,
                conversation_id=conversation_id,
                content="Cancel this request",
                tier="low",
                attachment_asset_ids=[],
                storage=_FakeObjectStorage(),
            )
        )
        assert await asyncio.to_thread(provider.started.wait, 5)
        assert provider.task is not None
        provider.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await send_task

    assert len(provider.calls) == 1
    with auth_db() as db:
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        user_message = db.scalar(
            select(ChatMessage).where(
                ChatMessage.tenant_id == auth_context["tenant_id"],
                ChatMessage.role == "user",
            )
        )
        entries = list(
            db.scalars(
                select(ReasoningLedgerEntry)
                .where(ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"])
                .order_by(ReasoningLedgerEntry.created_at.asc())
            )
        )
        assert wallet.available_credits < Decimal("100")
        assert wallet.reserved_credits > Decimal("0")
        assert user_message.status == "pending"
        assert [entry.entry_type for entry in entries] == ["topup", "reserve"]

    provider.release.set()
    assert await asyncio.to_thread(provider.finished.wait, 5)
    recovered_at = datetime.now(UTC) + timedelta(hours=1)
    with auth_db() as db:
        recovered = aibrain.recover_stale_reasoning_reservations(
            db,
            cutoff=recovered_at,
            recovered_at=recovered_at,
        )
        db.commit()
        assert recovered == 1

    with auth_db() as db:
        wallet = db.get(ReasoningWallet, auth_context["tenant_id"])
        user_message = db.scalar(
            select(ChatMessage).where(
                ChatMessage.tenant_id == auth_context["tenant_id"],
                ChatMessage.role == "user",
            )
        )
        entries = list(
            db.scalars(
                select(ReasoningLedgerEntry)
                .where(ReasoningLedgerEntry.tenant_id == auth_context["tenant_id"])
                .order_by(ReasoningLedgerEntry.created_at.asc())
            )
        )
        assert wallet.available_credits == Decimal("100")
        assert wallet.reserved_credits == Decimal("0")
        assert user_message.status == "failed"
        assert user_message.error_code == "AIBRAIN_RESERVATION_EXPIRED"
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
