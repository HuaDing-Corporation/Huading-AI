import asyncio
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import get_args

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.db.models import CopyDraft, CreditRate, Subscription, UsageRecord, VideoTask
from app.main import app
from app.schemas.copy import CopyRewriteRequest
from app.schemas.response import OperationOutcome


def test_operation_outcome_contract_is_frozen() -> None:
    fields = OperationOutcome.model_fields
    assert set(fields) == {"operation", "status"}
    assert fields["operation"].annotation is str
    assert get_args(fields["status"].annotation) == ("succeeded", "failed")
    assert all(field.is_required() for field in fields.values())
    assert OperationOutcome(operation="rewrite", status="succeeded").model_dump() == {
        "operation": "rewrite",
        "status": "succeeded",
    }
    assert OperationOutcome(operation="topics", status="failed").model_dump() == {
        "operation": "topics",
        "status": "failed",
    }


def _register_tenant(client: TestClient, slug: str) -> dict:
    resp = client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_slug": slug,
            "tenant_name": slug.title(),
            "email": f"owner-{slug}@example.com",
            "password": "secret-pass",
        },
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    return {
        "headers": {"Authorization": f"Bearer {data['token']['access_token']}"},
        "tenant_id": data["tenant"]["id"],
    }


def _patch_copy_llm(monkeypatch, results: list[str], payloads: list[dict]) -> None:
    from app.services import copy as copy_service

    class _FakeDeepSeek:
        async def generate_text(self, payload: dict):
            payloads.append(payload)
            return {"text": "\n".join(results)}

    monkeypatch.setattr(copy_service.settings, "engine_llm_api_key", "k")
    monkeypatch.setattr(copy_service.settings, "engine_llm_base_url", "https://deepseek.test")
    monkeypatch.setattr(copy_service.settings, "engine_llm_model", "m")
    monkeypatch.setattr(
        copy_service,
        "resolve",
        lambda _db, *, tenant_id, capability: _FakeDeepSeek(),
        raising=False,
    )


@contextmanager
def _capture_sqlite_dml(auth_db) -> Iterator[list[str]]:
    dml_action_names = {
        sqlite3.SQLITE_INSERT: "INSERT",
        sqlite3.SQLITE_UPDATE: "UPDATE",
        sqlite3.SQLITE_DELETE: "DELETE",
    }
    operations: list[str] = []

    def authorize(action_code, table_name, _column, _database, _trigger):
        operation = dml_action_names.get(action_code)
        if operation is not None:
            operations.append(f"{operation} {table_name}")
        return sqlite3.SQLITE_OK

    engine = auth_db.kw["bind"]
    with engine.connect() as connection:
        driver_connection = connection.connection.driver_connection
        driver_connection.set_authorizer(authorize)

    try:
        yield operations
    finally:
        driver_connection.set_authorizer(None)


def test_copy_estimate_matches_decimal_tenant_rate_and_actual_charges(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    with auth_db() as db:
        db.add_all(
            [
                CreditRate(
                    tenant_id=None,
                    capability="llm",
                    unit="call",
                    credits_per_unit=Decimal("5.0000"),
                ),
                CreditRate(
                    tenant_id=auth_context["tenant_id"],
                    capability="llm",
                    unit="call",
                    credits_per_unit=Decimal("7.1000"),
                ),
            ]
        )
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        used_before = subscription.quota_credits_used
        db.commit()

    payloads: list[dict] = []
    _patch_copy_llm(monkeypatch, ["候选文案"], payloads)
    client = TestClient(app)
    response = client.post(
        "/api/v1/copy/estimate",
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "estimated_credits": 24,
        "unit": "credits",
        "note": "本报价包含文案改写、标题和话题三项。",
        "breakdown": [
            {"operation": "rewrite", "estimated_credits": 8},
            {"operation": "titles", "estimated_credits": 8},
            {"operation": "topics", "estimated_credits": 8},
        ],
    }

    generation_responses = [
        client.post(
            "/api/v1/copy/rewrite",
            json={"source_text": "卖点", "mode": "smart"},
            headers=auth_context["headers"],
        ),
        client.post(
            "/api/v1/copy/titles",
            json={"source_text": "卖点", "n": 1},
            headers=auth_context["headers"],
        ),
        client.post(
            "/api/v1/copy/topics",
            json={"source_text": "卖点", "n": 1},
            headers=auth_context["headers"],
        ),
    ]

    assert [item.status_code for item in generation_responses] == [200, 200, 200]
    assert len(payloads) == 3
    with auth_db() as db:
        records = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == auth_context["tenant_id"],
                    UsageRecord.capability == "llm",
                )
            )
        )
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        assert len(records) == 3
        assert all(record.status == "settled" for record in records)
        assert subscription.quota_credits_used - used_before == response.json()["data"][
            "estimated_credits"
        ]
        assert subscription.quota_credits_reserved == 0


def test_copy_estimate_is_read_only(
    auth_context,
    auth_db,
) -> None:
    def snapshot() -> dict[str, object]:
        with auth_db() as db:
            subscription = db.scalar(
                select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
            )
            return {
                "quota_used": subscription.quota_credits_used,
                "quota_reserved": subscription.quota_credits_reserved,
                "usage_records": db.scalar(select(func.count()).select_from(UsageRecord)),
                "video_tasks": db.scalar(select(func.count()).select_from(VideoTask)),
                "copy_drafts": db.scalar(select(func.count()).select_from(CopyDraft)),
            }

    before = snapshot()
    with _capture_sqlite_dml(auth_db) as dml_operations:
        response = TestClient(app).post(
            "/api/v1/copy/estimate",
            headers=auth_context["headers"],
        )

    assert response.status_code == 200
    assert response.json()["data"]["estimated_credits"] == sum(
        item["estimated_credits"] for item in response.json()["data"]["breakdown"]
    )
    assert dml_operations == []
    assert snapshot() == before


@pytest.mark.parametrize(
    ("statement", "expected_operation"),
    [
        (
            """
            WITH target AS (
                SELECT id
                FROM subscriptions
                WHERE tenant_id = :tenant_id
            )
            UPDATE subscriptions
            SET quota_credits_used = quota_credits_used
            WHERE id IN (SELECT id FROM target)
            """,
            "UPDATE subscriptions",
        ),
        (
            """
            WITH source AS (
                SELECT :tenant_id AS id
                WHERE 0
            )
            INSERT INTO subscriptions (id)
            SELECT id FROM source
            """,
            "INSERT subscriptions",
        ),
        (
            """
            WITH target AS (
                SELECT id
                FROM subscriptions
                WHERE tenant_id = :tenant_id AND 0
            )
            DELETE FROM subscriptions
            WHERE id IN (SELECT id FROM target)
            """,
            "DELETE subscriptions",
        ),
    ],
    ids=["update", "insert", "delete"],
)
def test_copy_read_only_guard_detects_cte_dml(
    statement: str,
    expected_operation: str,
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.api.v1.routes import copy as copy_routes

    estimate_without_mutation = copy_routes.estimate_copy_quota

    def estimate_with_cte_dml(db, *, tenant_id, count):
        db.execute(text(statement), {"tenant_id": tenant_id})
        return estimate_without_mutation(db, tenant_id=tenant_id, count=count)

    monkeypatch.setattr(copy_routes, "estimate_copy_quota", estimate_with_cte_dml)
    with _capture_sqlite_dml(auth_db) as dml_operations:
        response = TestClient(app).post(
            "/api/v1/copy/estimate",
            headers=auth_context["headers"],
        )

    assert response.status_code == 200
    assert expected_operation in dml_operations


@pytest.mark.parametrize(
    ("path", "payload", "operation"),
    [
        (
            "/api/v1/copy/rewrite",
            {"source_text": "卖点", "mode": "smart"},
            "rewrite",
        ),
        ("/api/v1/copy/titles", {"source_text": "卖点", "n": 1}, "titles"),
        ("/api/v1/copy/topics", {"source_text": "卖点", "n": 1}, "topics"),
    ],
)
def test_copy_generation_endpoints_share_one_reserved_then_settled_usage_record(
    path: str,
    payload: dict,
    operation: str,
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    payloads: list[dict] = []
    _patch_copy_llm(monkeypatch, ["候选文案"], payloads)
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        used_before = subscription.quota_credits_used

    response = TestClient(app).post(
        path,
        json=payload,
        headers=auth_context["headers"],
    )

    assert response.status_code == 200
    assert response.json()["data"]["outcome"] == {
        "operation": operation,
        "status": "succeeded",
    }
    assert len(payloads) == 1
    with auth_db() as db:
        records = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == auth_context["tenant_id"],
                    UsageRecord.capability == "llm",
                )
            )
        )
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        assert len(records) == 1
        assert records[0].status == "settled"
        assert records[0].credits == Decimal("1")
        assert subscription.quota_credits_used == used_before + 1
        assert subscription.quota_credits_reserved == 0


@pytest.mark.parametrize(
    ("path", "payload", "operation"),
    [
        (
            "/api/v1/copy/rewrite",
            {"source_text": "余额不足", "mode": "smart"},
            "rewrite",
        ),
        ("/api/v1/copy/titles", {"source_text": "余额不足", "n": 1}, "titles"),
        ("/api/v1/copy/topics", {"source_text": "余额不足", "n": 1}, "topics"),
    ],
)
def test_copy_generation_rejects_insufficient_quota_before_provider_call(
    path: str,
    payload: dict,
    operation: str,
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    payloads: list[dict] = []
    _patch_copy_llm(monkeypatch, ["must not run"], payloads)
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        subscription.quota_credits_used = subscription.quota_credits_total
        db.commit()

    response = TestClient(app).post(
        path,
        json=payload,
        headers=auth_context["headers"],
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TENANT_QUOTA_EXCEEDED"
    assert response.json()["error"]["outcome"] == {
        "operation": operation,
        "status": "failed",
    }
    assert response.json()["error"]["detail"] is None
    assert payloads == []
    with auth_db() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0


def test_copy_generation_partial_success_only_bills_the_successful_endpoint(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    payloads: list[dict] = []
    _patch_copy_llm(monkeypatch, ["候选文案"], payloads)
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        subscription.quota_credits_used = subscription.quota_credits_total - 1
        db.commit()

    client = TestClient(app)
    rewrite = client.post(
        "/api/v1/copy/rewrite",
        json={"source_text": "只剩一积分", "mode": "smart"},
        headers=auth_context["headers"],
    )
    titles = client.post(
        "/api/v1/copy/titles",
        json={"source_text": "只剩一积分", "n": 1},
        headers=auth_context["headers"],
    )
    topics = client.post(
        "/api/v1/copy/topics",
        json={"source_text": "只剩一积分", "n": 1},
        headers=auth_context["headers"],
    )

    assert rewrite.status_code == 200
    assert rewrite.json()["data"]["outcome"] == {
        "operation": "rewrite",
        "status": "succeeded",
    }
    assert titles.status_code == 403
    assert titles.json()["error"]["outcome"] == {
        "operation": "titles",
        "status": "failed",
    }
    assert topics.status_code == 403
    assert topics.json()["error"]["outcome"] == {
        "operation": "topics",
        "status": "failed",
    }
    assert len(payloads) == 1
    with auth_db() as db:
        records = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == auth_context["tenant_id"],
                    UsageRecord.capability == "llm",
                )
            )
        )
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        assert len(records) == 1
        assert records[0].status == "settled"
        assert records[0].credits == Decimal("1")
        assert subscription.quota_credits_reserved == 0


def test_copy_provider_failure_releases_reserved_quota(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services import copy as copy_service

    calls: list[dict] = []

    class _FailingDeepSeek:
        async def generate_text(self, payload: dict):
            calls.append(payload)
            raise RuntimeError("provider failed")

    monkeypatch.setattr(copy_service.settings, "engine_llm_api_key", "k")
    monkeypatch.setattr(copy_service.settings, "engine_llm_base_url", "https://deepseek.test")
    monkeypatch.setattr(copy_service.settings, "engine_llm_model", "deepseek-v4-flash")
    monkeypatch.setattr(copy_service, "resolve", lambda *_args, **_kwargs: _FailingDeepSeek())
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        used_before = subscription.quota_credits_used

    response = TestClient(app).post(
        "/api/v1/copy/rewrite",
        json={"source_text": "触发上游失败", "mode": "smart"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 502
    assert response.status_code != 500
    assert response.json()["error"]["outcome"] == {
        "operation": "rewrite",
        "status": "failed",
    }
    assert len(calls) == 1
    _assert_single_released_copy_reservation(auth_db, auth_context["tenant_id"])
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        assert subscription.quota_credits_used == used_before


def test_copy_response_failure_after_settlement_keeps_charge_and_returns_failed_outcome(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.api.v1.routes import copy as copy_routes

    payloads: list[dict] = []
    _patch_copy_llm(monkeypatch, ["候选标题"], payloads)

    def fail_response(*_args, **_kwargs):
        raise RuntimeError("response construction failed")

    monkeypatch.setattr(copy_routes, "ok", fail_response)
    with auth_db() as db:
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        used_before = subscription.quota_credits_used

    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/copy/titles",
        json={"source_text": "卖点", "n": 1},
        headers=auth_context["headers"],
    )

    assert response.status_code == 500
    assert response.json()["error"]["outcome"] == {
        "operation": "titles",
        "status": "failed",
    }
    assert len(payloads) == 1
    with auth_db() as db:
        records = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == auth_context["tenant_id"],
                    UsageRecord.capability == "llm",
                )
            )
        )
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        assert len(records) == 1
        assert records[0].status == "settled"
        assert subscription.quota_credits_used == used_before + records[0].credits
        assert subscription.quota_credits_reserved == 0


def test_copy_timeout_releases_reserved_quota(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services import copy as copy_service

    payloads: list[dict] = []
    _patch_copy_llm(monkeypatch, ["unused"], payloads)

    async def timeout(*_args, **_kwargs):
        raise TimeoutError("provider timeout")

    monkeypatch.setattr(copy_service, "invoke", timeout)

    response = TestClient(app).post(
        "/api/v1/copy/titles",
        json={"source_text": "触发超时", "n": 1},
        headers=auth_context["headers"],
    )

    assert response.status_code == 502
    assert response.json()["error"]["outcome"] == {
        "operation": "titles",
        "status": "failed",
    }
    _assert_single_released_copy_reservation(auth_db, auth_context["tenant_id"])


def test_copy_result_validation_failure_releases_and_keeps_provider_cost(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services import copy as copy_service

    class _EmptyDeepSeek:
        async def generate_text(self, _payload: dict):
            return {
                "text": "",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "usage": {
                    "prompt_tokens": 100_000,
                    "completion_tokens": 50_000,
                    "total_tokens": 150_000,
                },
            }

    monkeypatch.setattr(copy_service.settings, "engine_llm_api_key", "k")
    monkeypatch.setattr(copy_service.settings, "engine_llm_base_url", "https://deepseek.test")
    monkeypatch.setattr(copy_service.settings, "engine_llm_model", "deepseek-v4-flash")
    monkeypatch.setattr(copy_service, "resolve", lambda *_args, **_kwargs: _EmptyDeepSeek())

    response = TestClient(app).post(
        "/api/v1/copy/topics",
        json={"source_text": "触发结果校验失败", "n": 1},
        headers=auth_context["headers"],
    )

    assert response.status_code == 502
    assert response.json()["error"]["outcome"] == {
        "operation": "topics",
        "status": "failed",
    }
    with auth_db() as db:
        record = db.scalar(
            select(UsageRecord).where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.capability == "llm",
            )
        )
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        assert record.status == "released"
        assert record.cost_cents > 0
        assert subscription.quota_credits_reserved == 0


def test_copy_cancellation_releases_reserved_quota(
    monkeypatch,
    auth_context,
    auth_db,
) -> None:
    from app.services import copy as copy_service

    payloads: list[dict] = []
    _patch_copy_llm(monkeypatch, ["unused"], payloads)

    async def cancel(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(copy_service, "invoke", cancel)
    with auth_db() as db:
        with pytest.raises(asyncio.CancelledError):
            copy_service.rewrite_copy(
                db,
                tenant_id=auth_context["tenant_id"],
                payload=CopyRewriteRequest(source_text="取消请求", mode="smart"),
            )

    _assert_single_released_copy_reservation(auth_db, auth_context["tenant_id"])


def _assert_single_released_copy_reservation(auth_db, tenant_id: str) -> None:
    with auth_db() as db:
        records = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == tenant_id,
                    UsageRecord.capability == "llm",
                )
            )
        )
        subscription = db.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        assert len(records) == 1
        assert records[0].status == "released"
        assert subscription.quota_credits_reserved == 0


def test_copy_rewrite_seedance_i2v_cleans_and_uses_duration_budget(
    monkeypatch,
    auth_context,
) -> None:
    payloads: list[dict] = []
    _patch_copy_llm(
        monkeypatch,
        [
            "【数字人主播脚本】",
            "（微笑，自然站姿，展示裤子）",
            "**姐妹们，这条裤子显瘦又舒服，现在下单更划算。**",
        ],
        payloads,
    )

    resp = TestClient(app).post(
        "/api/v1/copy/rewrite",
        json={
            "source_text": "高腰阔腿裤，显瘦，通勤休闲都能穿",
            "mode": "smart",
            "video_mode": "seedance_i2v",
            "duration_sec": 10,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["data"] == {
        "results": [{"text": "姐妹们，这条裤子显瘦又舒服，现在下单更划算。"}],
        "outcome": {"operation": "rewrite", "status": "succeeded"},
    }
    assert body["request_id"]

    payload = payloads[0]
    assert payload["topic"] == "高腰阔腿裤，显瘦，通勤休闲都能穿"
    assert payload["video_mode"] == "seedance_i2v"
    assert payload["target_duration_sec"] == 10
    assert payload["target_chars_min"] == 50
    assert payload["target_chars_max"] == 60
    assert "改写" in payload["user_prompt"]
    assert "50-60字" in payload["user_prompt"]


def test_copy_rewrite_auto_clamps_n_and_returns_multiple_candidates(
    monkeypatch,
    auth_context,
) -> None:
    payloads: list[dict] = []
    _patch_copy_llm(
        monkeypatch,
        [
            "1. 第一条改写",
            "2. 第二条改写",
            "3. 第三条改写",
            "4. 第四条改写",
            "5. 第五条改写",
            "6. 不应返回",
        ],
        payloads,
    )

    resp = TestClient(app).post(
        "/api/v1/copy/rewrite",
        json={
            "source_text": "原始卖点",
            "mode": "auto",
            "n": 99,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "results": [
            {"text": "第一条改写"},
            {"text": "第二条改写"},
            {"text": "第三条改写"},
            {"text": "第四条改写"},
            {"text": "第五条改写"},
        ],
        "outcome": {"operation": "rewrite", "status": "succeeded"},
    }
    assert payloads[0]["candidate_count"] == 5
    assert "生成5条" in payloads[0]["user_prompt"]


def test_copy_titles_generates_title_candidates(monkeypatch, auth_context) -> None:
    payloads: list[dict] = []
    _patch_copy_llm(
        monkeypatch,
        [
            "1. 质感通勤裤",
            "2. 显瘦不费力",
            "3. 一条穿出高级感",
        ],
        payloads,
    )

    resp = TestClient(app).post(
        "/api/v1/copy/titles",
        json={
            "source_text": "高腰阔腿裤，显瘦，通勤休闲都能穿",
            "n": 3,
            "style": "短句",
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "titles": ["质感通勤裤", "显瘦不费力", "一条穿出高级感"],
        "outcome": {"operation": "titles", "status": "succeeded"},
    }
    assert payloads[0]["candidate_count"] == 3
    assert "短句" in payloads[0]["user_prompt"]


def test_copy_topics_generates_hash_tag_candidates(monkeypatch, auth_context) -> None:
    payloads: list[dict] = []
    _patch_copy_llm(
        monkeypatch,
        [
            "1. 通勤穿搭",
            "2. #显瘦裤子",
            "3. 高级感穿搭",
        ],
        payloads,
    )

    resp = TestClient(app).post(
        "/api/v1/copy/topics",
        json={
            "source_text": "高腰阔腿裤，显瘦，通勤休闲都能穿",
            "n": 3,
        },
        headers=auth_context["headers"],
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "topics": ["#通勤穿搭", "#显瘦裤子", "#高级感穿搭"],
        "outcome": {"operation": "topics", "status": "succeeded"},
    }
    assert payloads[0]["candidate_count"] == 3
    assert "话题" in payloads[0]["user_prompt"]


def test_copy_rewrite_validation_errors_return_m2_422(auth_context) -> None:
    client = TestClient(app)

    blank = client.post(
        "/api/v1/copy/rewrite",
        json={"source_text": "   ", "mode": "smart"},
        headers=auth_context["headers"],
    )
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "VALIDATION_ERROR"
    assert blank.json()["error"]["outcome"] == {
        "operation": "rewrite",
        "status": "failed",
    }
    assert blank.json()["error"]["detail"] == blank.json()["error"]["details"]

    too_long = client.post(
        "/api/v1/copy/rewrite",
        json={"source_text": "x" * 4001, "mode": "smart"},
        headers=auth_context["headers"],
    )
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "VALIDATION_ERROR"
    assert too_long.json()["error"]["outcome"] == {
        "operation": "rewrite",
        "status": "failed",
    }
    assert too_long.json()["error"]["detail"] == too_long.json()["error"]["details"]

    missing_instruction = client.post(
        "/api/v1/copy/rewrite",
        json={"source_text": "原始文案", "mode": "custom"},
        headers=auth_context["headers"],
    )
    assert missing_instruction.status_code == 422
    assert missing_instruction.json()["error"]["code"] == "VALIDATION_ERROR"
    assert missing_instruction.json()["error"]["outcome"] == {
        "operation": "rewrite",
        "status": "failed",
    }
    assert missing_instruction.json()["error"]["detail"] == (
        missing_instruction.json()["error"]["details"]
    )


def test_copy_generation_auth_failure_identifies_the_requested_operation() -> None:
    response = TestClient(app).post(
        "/api/v1/copy/topics",
        json={"source_text": "未登录", "n": 1},
    )

    assert response.status_code == 401
    assert response.json()["error"]["outcome"] == {
        "operation": "topics",
        "status": "failed",
    }
    assert response.json()["error"]["detail"] is None


def test_copy_generation_errors_are_stable_and_friendly(monkeypatch, auth_context) -> None:
    from app.services import copy as copy_service

    client = TestClient(app)

    monkeypatch.setattr(copy_service.settings, "engine_llm_api_key", "")
    monkeypatch.setattr(copy_service.settings, "engine_llm_base_url", "")
    monkeypatch.setattr(copy_service.settings, "engine_llm_model", "")

    not_configured = client.post(
        "/api/v1/copy/rewrite",
        json={"source_text": "原始文案", "mode": "smart"},
        headers=auth_context["headers"],
    )
    assert not_configured.status_code == 503
    assert not_configured.json()["error"]["code"] == "LLM_NOT_CONFIGURED"

    class _FailingDeepSeek:
        async def generate_text(self, payload: dict):
            raise RuntimeError('{"provider_raw":"do not expose"}')

    monkeypatch.setattr(copy_service.settings, "engine_llm_api_key", "k")
    monkeypatch.setattr(copy_service.settings, "engine_llm_base_url", "https://deepseek.test")
    monkeypatch.setattr(copy_service.settings, "engine_llm_model", "m")
    monkeypatch.setattr(
        copy_service,
        "resolve",
        lambda _db, *, tenant_id, capability: _FailingDeepSeek(),
        raising=False,
    )

    failed = client.post(
        "/api/v1/copy/rewrite",
        json={"source_text": "原始文案", "mode": "smart"},
        headers=auth_context["headers"],
    )
    body = failed.json()
    assert failed.status_code == 502
    assert body["error"]["code"] == "COPY_GEN_FAILED"
    assert body["error"]["message"] == "Copy generation failed."
    assert "provider_raw" not in str(body)


def test_copy_rewrite_does_not_create_video_task(monkeypatch, auth_context, auth_db) -> None:
    payloads: list[dict] = []
    _patch_copy_llm(monkeypatch, ["改写文案"], payloads)

    resp = TestClient(app).post(
        "/api/v1/copy/rewrite",
        json={"source_text": "原始文案", "mode": "smart"},
        headers=auth_context["headers"],
    )

    assert resp.status_code == 200
    with auth_db() as db:
        video_task_count = db.scalar(select(func.count()).select_from(VideoTask))
    assert video_task_count == 0


def test_copy_drafts_crud_is_tenant_scoped_and_soft_deleted(auth_context) -> None:
    client = TestClient(app)
    tenant_b = _register_tenant(client, "copy-tenant-b")

    created = client.post(
        "/api/v1/copy/drafts",
        json={
            "source_text": "原始卖点",
            "result_text": "改写文案",
            "titles": ["质感通勤裤"],
            "topics": ["#通勤穿搭"],
            "mode": "smart",
            "target_platform": "douyin",
        },
        headers=auth_context["headers"],
    )
    assert created.status_code == 201
    draft = created.json()["data"]
    draft_id = draft["id"]
    assert draft["source_text"] == "原始卖点"
    assert draft["result_text"] == "改写文案"
    assert draft["titles"] == ["质感通勤裤"]
    assert draft["topics"] == ["#通勤穿搭"]
    assert draft["mode"] == "smart"
    assert draft["target_platform"] == "douyin"
    assert draft["created_at"]
    assert draft["deleted_at"] is None

    listing = client.get("/api/v1/copy/drafts", headers=auth_context["headers"])
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 1
    assert listing.json()["data"]["items"][0]["id"] == draft_id

    detail = client.get(f"/api/v1/copy/drafts/{draft_id}", headers=auth_context["headers"])
    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == draft_id

    cross_detail = client.get(f"/api/v1/copy/drafts/{draft_id}", headers=tenant_b["headers"])
    assert cross_detail.status_code == 404
    assert cross_detail.json()["error"]["code"] == "COPY_DRAFT_NOT_FOUND"

    cross_delete = client.delete(f"/api/v1/copy/drafts/{draft_id}", headers=tenant_b["headers"])
    assert cross_delete.status_code == 404
    assert cross_delete.json()["error"]["code"] == "COPY_DRAFT_NOT_FOUND"

    deleted = client.delete(f"/api/v1/copy/drafts/{draft_id}", headers=auth_context["headers"])
    assert deleted.status_code == 200
    assert deleted.json()["data"]["id"] == draft_id
    assert deleted.json()["data"]["deleted_at"]

    after_delete = client.get("/api/v1/copy/drafts", headers=auth_context["headers"])
    assert after_delete.status_code == 200
    assert after_delete.json()["data"] == {"items": [], "total": 0}

    hidden = client.get(f"/api/v1/copy/drafts/{draft_id}", headers=auth_context["headers"])
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "COPY_DRAFT_NOT_FOUND"


def test_copy_drafts_clear_soft_deletes_active_drafts(auth_context, auth_db) -> None:
    client = TestClient(app)
    tenant_b = _register_tenant(client, "copy-clear-b")
    with auth_db() as db:
        tenant_a_drafts = [
            CopyDraft(
                tenant_id=auth_context["tenant_id"],
                source_text=f"source {index}",
                result_text=f"result {index}",
                mode="smart",
            )
            for index in range(2)
        ]
        other_draft = CopyDraft(
            tenant_id=tenant_b["tenant_id"],
            source_text="other source",
            result_text="other result",
            mode="smart",
        )
        db.add_all([*tenant_a_drafts, other_draft])
        db.commit()
        tenant_a_ids = [draft.id for draft in tenant_a_drafts]
        other_id = other_draft.id

    cleared = client.delete("/api/v1/copy/drafts", headers=auth_context["headers"])

    assert cleared.status_code == 200
    assert cleared.json()["data"] == {"deleted_count": 2}
    listing = client.get("/api/v1/copy/drafts", headers=auth_context["headers"])
    assert listing.status_code == 200
    assert listing.json()["data"] == {"items": [], "total": 0}
    with auth_db() as db:
        tenant_drafts = db.scalars(select(CopyDraft).where(CopyDraft.id.in_(tenant_a_ids))).all()
        other = db.get(CopyDraft, other_id)
    assert {draft.id for draft in tenant_drafts} == set(tenant_a_ids)
    assert all(draft.deleted_at is not None for draft in tenant_drafts)
    assert other.deleted_at is None


def test_copy_draft_create_soft_prunes_active_history_to_20(auth_context, auth_db) -> None:
    client = TestClient(app)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    with auth_db() as db:
        for index in range(20):
            db.add(
                CopyDraft(
                    id=f"old-copy-{index:02d}",
                    tenant_id=auth_context["tenant_id"],
                    source_text=f"old source {index}",
                    result_text=f"old result {index}",
                    mode="smart",
                    created_at=base_time + timedelta(minutes=index),
                )
            )
        db.commit()

    created = client.post(
        "/api/v1/copy/drafts",
        json={
            "source_text": "new source",
            "result_text": "new result",
            "mode": "smart",
        },
        headers=auth_context["headers"],
    )

    assert created.status_code == 201
    new_id = created.json()["data"]["id"]
    listing = client.get("/api/v1/copy/drafts?limit=100", headers=auth_context["headers"])
    assert listing.status_code == 200
    active_ids = [item["id"] for item in listing.json()["data"]["items"]]
    assert len(active_ids) == 20
    assert new_id in active_ids
    assert "old-copy-00" not in active_ids
    with auth_db() as db:
        oldest = db.get(CopyDraft, "old-copy-00")
    assert oldest is not None
    assert oldest.deleted_at is not None
