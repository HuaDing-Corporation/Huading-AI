from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import VideoTask
from app.main import app


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
        "results": [{"text": "姐妹们，这条裤子显瘦又舒服，现在下单更划算。"}]
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
        ]
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
        "titles": ["质感通勤裤", "显瘦不费力", "一条穿出高级感"]
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
    assert resp.json()["data"] == {"topics": ["#通勤穿搭", "#显瘦裤子", "#高级感穿搭"]}
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

    too_long = client.post(
        "/api/v1/copy/rewrite",
        json={"source_text": "x" * 4001, "mode": "smart"},
        headers=auth_context["headers"],
    )
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "VALIDATION_ERROR"

    missing_instruction = client.post(
        "/api/v1/copy/rewrite",
        json={"source_text": "原始文案", "mode": "custom"},
        headers=auth_context["headers"],
    )
    assert missing_instruction.status_code == 422
    assert missing_instruction.json()["error"]["code"] == "VALIDATION_ERROR"


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
