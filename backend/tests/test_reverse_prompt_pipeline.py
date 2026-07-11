from __future__ import annotations

import importlib.util
import json as jsonlib
import os
import subprocess
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql

from app.core.exceptions import AppError
from app.db.models import (
    Asset,
    CreditRate,
    ProviderConfig,
    ReversePromptJob,
    Subscription,
    Tenant,
    UsageRecord,
    User,
)
from app.main import app
from app.providers.reverse_prompt.apimart_gemini import (
    APIMartGeminiReversePromptProvider,
    normalize_product_validation_payload,
)
from app.services.reverse_prompt_video import extract_uniform_video_frames, frame_timestamps


def test_reverse_prompt_seed_provider_id_fits_provider_config_column():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260706_0017_reverse_prompt_jobs.py"
    )
    spec = importlib.util.spec_from_file_location("reverse_prompt_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert len(migration._PROVIDER_ID) <= 36


def test_reverse_prompt_seed_credit_rate_defaults_to_30():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260706_0017_reverse_prompt_jobs.py"
    )
    spec = importlib.util.spec_from_file_location("reverse_prompt_migration_rate", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    class _Batch:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def drop_constraint(self, *args, **kwargs):
            return None

        def create_check_constraint(self, *args, **kwargs):
            return None

    class _Op:
        def __init__(self):
            self.inserted = []

        def create_table(self, *args, **kwargs):
            return None

        def create_index(self, *args, **kwargs):
            return None

        def batch_alter_table(self, *args, **kwargs):
            return _Batch()

        def bulk_insert(self, table, rows):
            self.inserted.append((table.name, rows))

    fake_op = _Op()
    migration.op = fake_op
    migration.upgrade()

    credit_rate_rows = [
        row
        for table_name, rows in fake_op.inserted
        if table_name == "credit_rates"
        for row in rows
    ]
    assert credit_rate_rows == [
        {
            "id": "reverse-prompt-call-rate",
            "tenant_id": None,
            "capability": "reverse_prompt",
            "unit": "call",
            "credits_per_unit": 30,
            "is_active": True,
        }
    ]


def test_reverse_prompt_video_migration_extends_billing_and_links_usage() -> None:
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260710_0024_reverse_prompt_video.py"
    )
    source = migration_path.read_text(encoding="utf-8")

    assert 'revision = "20260710_0024"' in source
    assert 'down_revision = "20260710_0023"' in source
    assert "reverse_prompt_video" in source
    assert "reverse_prompt_job_id" in source
    assert "ix_usage_records_reverse_prompt_status" in source
    assert "UPDATE usage_records" in source
    assert "UPDATE credit_rates" in source


def test_reverse_prompt_video_quota_uses_configurable_fallback_and_tenant_override(
    auth_db,
    auth_context,
    monkeypatch,
) -> None:
    env_example = (Path(__file__).parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "ENGINE_REVERSE_PROMPT_VIDEO_CREDITS=100" in env_example

    from app.core.config import settings
    from app.services.quota import estimate_reverse_prompt_video_quota

    monkeypatch.setattr(settings, "engine_reverse_prompt_video_credits", 125.0)
    db = auth_db()
    fallback = estimate_reverse_prompt_video_quota(
        db,
        tenant_id=auth_context["tenant_id"],
    )
    assert fallback.estimated_credits == Decimal("125.00")

    db.add(
        CreditRate(
            tenant_id=auth_context["tenant_id"],
            capability="reverse_prompt_video",
            unit="call",
            credits_per_unit=Decimal("87.5000"),
            is_active=True,
        )
    )
    db.flush()
    tenant_rate = estimate_reverse_prompt_video_quota(
        db,
        tenant_id=auth_context["tenant_id"],
    )
    assert tenant_rate.estimated_credits == Decimal("87.50")
    db.close()


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class _SseResponse:
    def __init__(self, lines: list[str | bytes], status_code: int = 200) -> None:
        self._lines = list(lines)
        self.status_code = status_code
        self.headers = {"content-type": "text/event-stream; charset=utf-8"}
        self.text = ""

    def json(self) -> dict:
        raise ValueError("streaming response is not JSON")

    def iter_lines(self):
        yield from self._lines


class _Session:
    def __init__(self, payloads: list[dict | _Response | _SseResponse]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict, timeout: float):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        payload = self.payloads.pop(0)
        if isinstance(payload, _Response | _SseResponse):
            return payload
        return _Response(payload)


def _chat_payload(
    content: str,
    *,
    usage: dict | None = None,
    credits: object | None = None,
) -> dict:
    payload = {
        "id": "chatcmpl-test",
        "choices": [{"message": {"content": content}}],
        "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
    }
    if credits is not None:
        payload["credits"] = credits
    return payload


JSON_CONTENT = """{
  "target_format": "seedance_2_0",
  "prompt_zh": "Premium perfume bottle on marble with soft morning light.",
  "prompt_en": "Glass perfume bottle on white marble, soft morning light.",
  "negative_prompt": "blurry, distorted logo",
  "style_tags": ["commercial", "soft light"],
  "camera": "close-up product shot",
  "lighting": "soft morning light",
  "composition": "centered product composition",
  "subject": "glass perfume bottle",
  "scene": "white marble tabletop",
  "motion_hint": "slow push-in camera move",
  "selling_points": ["crystal clear bottle", "premium texture"],
  "text_in_media": ["PARFUM"],
  "disclaimer": "Text may be approximate; verify brand and label details before reuse.",
  "confidence": 0.86
}"""

VIDEO_JSON_CONTENT = """{
  "target_format": "seedance_2_0",
  "prompt_zh": "香水瓶广告，镜头缓慢推进。",
  "prompt_en": "Perfume bottle commercial with a slow push-in.",
  "negative_prompt": "blurry, distorted logo",
  "style_tags": ["commercial", "premium"],
  "camera": "close-up",
  "lighting": "soft studio light",
  "composition": "centered",
  "subject": "glass perfume bottle",
  "scene": "white marble tabletop",
  "motion_hint": "slow push-in",
  "selling_points": ["premium texture"],
  "text_in_media": ["PARFUM"],
  "disclaimer": "Verify visible text before reuse.",
  "confidence": 0.9,
  "video_analysis": {
    "duration_sec": 23.8,
    "pacing": "variable",
    "shot_list": [
      {
        "index": 0,
        "start_sec": 0,
        "end_sec": 12,
        "visual": "A perfume bottle appears on marble.",
        "camera": "close-up",
        "motion": "slow push-in",
        "transition": "cut"
      },
      {
        "index": 1,
        "start_sec": 12,
        "end_sec": 24,
        "visual": "The logo catches a soft highlight.",
        "camera": "detail shot",
        "motion": "small orbit",
        "transition": "fade"
      }
    ],
    "audio_transcript": "model must not populate phase-one audio",
    "bgm_style": "model must not populate phase-one audio"
  }
}"""

PRODUCT_IDENTITY_CONTENT = """{
  "product_identity": {
    "main_color": "celadon green",
    "material": "ceramic",
    "glaze": "glossy celadon glaze",
    "decorative_trim": "gold rim",
    "shape": "round plate, bowl, and handled cup",
    "key_pattern": "solid green with no blue marble veining"
  }
}"""

PRODUCT_MISMATCH_CONTENT = """{
  "validation": {
    "status": "failed",
    "main_color_match": false,
    "pattern_match": false,
    "shape_match": true,
    "reason": "The rendered product is blue-white marble instead of celadon green."
  }
}"""

PRODUCT_MATCH_CONTENT = """{
  "validation": {
    "status": "passed",
    "main_color_match": true,
    "pattern_match": true,
    "shape_match": true,
    "reason": "The rendered product preserves the celadon color, plain glaze, and shape."
  }
}"""

PRODUCT_SHAPE_ADVISORY_CONTENT = """{
  "validation": {
    "status": "failed",
    "main_color_match": true,
    "pattern_match": true,
    "shape_match": false,
    "reason": "The product color and pattern match, but the display omits one cup."
  }
}"""


def test_apimart_gemini_analyzes_structured_product_identity() -> None:
    session = _Session([_chat_payload(PRODUCT_IDENTITY_CONTENT)])
    provider = APIMartGeminiReversePromptProvider(api_key="api-test-key", session=session)

    result = provider.analyze_product_identity_sync(
        {
            "image_url": "https://assets.test/green-celadon.png",
            "source_product_image_id": "product-green-celadon",
        }
    )

    assert result["product_identity"] == {
        "main_color": "celadon green",
        "material": "ceramic",
        "glaze": "glossy celadon glaze",
        "decorative_trim": "gold rim",
        "shape": "round plate, bowl, and handled cup",
        "key_pattern": "solid green with no blue marble veining",
    }
    assert result["prompt_tokens"] == 1000
    instruction = session.calls[0]["json"]["messages"][-1]["content"][0]["text"]
    assert "product_identity" in instruction
    assert "main_color" in instruction
    assert "glaze" in instruction
    assert "shape" in instruction


def test_apimart_gemini_analyzes_video_frames_with_frozen_contract() -> None:
    session = _Session([_chat_payload(VIDEO_JSON_CONTENT)])
    provider = APIMartGeminiReversePromptProvider(api_key="api-test-key", session=session)
    frame_urls = [f"data:image/jpeg;base64,frame-{index}" for index in range(8)]
    timestamps_sec = [1.5, 4.5, 7.5, 10.5, 13.5, 16.5, 19.5, 22.5]

    result = provider.reverse_video_frames_sync(
        {
            "image_urls": frame_urls,
            "timestamps_sec": timestamps_sec,
            "duration_sec": 24.0,
        }
    )

    assert result["video_analysis"] == {
        "duration_sec": 24.0,
        "pacing": "variable",
        "shot_list": [
            {
                "index": 0,
                "start_sec": 0.0,
                "end_sec": 12.0,
                "visual": "A perfume bottle appears on marble.",
                "camera": "close-up",
                "motion": "slow push-in",
                "transition": "cut",
            },
            {
                "index": 1,
                "start_sec": 12.0,
                "end_sec": 24.0,
                "visual": "The logo catches a soft highlight.",
                "camera": "detail shot",
                "motion": "small orbit",
                "transition": "fade",
            },
        ],
        "audio_transcript": None,
        "bgm_style": None,
    }
    content = session.calls[0]["json"]["messages"][-1]["content"]
    assert [part["image_url"]["url"] for part in content[1:]] == frame_urls
    instruction = content[0]["text"]
    assert "24.0 seconds" in instruction
    assert "1.5, 4.5, 7.5" in instruction
    assert "audio_transcript" in instruction


def test_video_frame_extraction_uses_midpoints_8_or_12_and_768px_jpeg() -> None:
    assert frame_timestamps(24.0) == [1.5, 4.5, 7.5, 10.5, 13.5, 16.5, 19.5, 22.5]
    assert len(frame_timestamps(30.0)) == 8
    assert len(frame_timestamps(30.001)) == 12

    calls: list[dict[str, object]] = []

    def fake_run(command, *, capture_output, check, timeout):
        calls.append(
            {
                "command": command,
                "capture_output": capture_output,
                "check": check,
                "timeout": timeout,
            }
        )
        return SimpleNamespace(returncode=0, stdout=b"jpeg-frame", stderr=b"")

    frames = extract_uniform_video_frames(
        Path("source.mp4"),
        duration_sec=24.0,
        run=fake_run,
    )

    assert frames == [b"jpeg-frame"] * 8
    assert len(calls) == 8
    for call, timestamp in zip(calls, frame_timestamps(24.0), strict=True):
        command = call["command"]
        assert command[command.index("-ss") + 1] == f"{timestamp:.3f}"
        assert command[command.index("-vf") + 1] == (
            "scale=768:768:force_original_aspect_ratio=decrease"
        )
        assert command[-5:] == ["-f", "image2pipe", "-vcodec", "mjpeg", "-"]
        assert call["timeout"] == 30.0


@pytest.fixture(scope="module")
def reverse_prompt_video_fixture(tmp_path_factory) -> Path:
    video_path = tmp_path_factory.mktemp("reverse-prompt-video") / "fixture.mp4"
    result = subprocess.run(
        [
            os.environ.get("FFMPEG_BINARY", "ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=800x450:r=2:d=31",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-y",
            str(video_path),
        ],
        capture_output=True,
        check=False,
        timeout=30.0,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert video_path.stat().st_size > 0
    return video_path


@pytest.mark.parametrize(("duration_sec", "expected_count"), [(2.0, 8), (31.0, 12)])
def test_extract_uniform_video_frames_runs_real_ffmpeg(
    reverse_prompt_video_fixture: Path,
    duration_sec: float,
    expected_count: int,
) -> None:
    frames = extract_uniform_video_frames(
        reverse_prompt_video_fixture,
        duration_sec=duration_sec,
    )

    assert len(frames) == expected_count
    for frame in frames:
        assert frame
        with Image.open(BytesIO(frame)) as image:
            image.load()
            assert image.format == "JPEG"
            assert max(image.size) == 768


def test_apimart_gemini_validator_rejects_product_color_and_pattern_mismatch() -> None:
    session = _Session([_chat_payload(PRODUCT_MISMATCH_CONTENT)])
    provider = APIMartGeminiReversePromptProvider(api_key="api-test-key", session=session)

    result = provider.validate_product_fidelity_sync(
        {
            "product_image_url": "https://assets.test/green-celadon.png",
            "rendered_image_url": "https://assets.test/blue-marble-output.png",
            "product_identity": {
                "main_color": "celadon green",
                "material": "ceramic",
                "glaze": "glossy celadon glaze",
                "decorative_trim": "gold rim",
                "shape": "round tableware set",
                "key_pattern": "solid green with no marble veining",
            },
        }
    )

    assert result["status"] == "failed"
    assert result["passed"] is False
    assert result["checks"] == {
        "main_color_match": False,
        "pattern_match": False,
        "shape_match": True,
    }
    assert "blue-white marble" in result["reason"]
    content = session.calls[0]["json"]["messages"][-1]["content"]
    assert len([part for part in content if part["type"] == "image_url"]) == 2
    instruction = content[0]["text"]
    assert "Image 1" in instruction and "ground truth" in instruction
    assert "Image 2" in instruction and "rendered candidate" in instruction
    assert "celadon green" in instruction


def test_apimart_gemini_validator_accepts_explicit_product_match() -> None:
    session = _Session([_chat_payload(PRODUCT_MATCH_CONTENT)])
    provider = APIMartGeminiReversePromptProvider(api_key="api-test-key", session=session)

    result = provider.validate_product_fidelity_sync(
        {
            "product_image_url": "https://assets.test/green-celadon.png",
            "rendered_image_url": "https://assets.test/green-celadon-output.png",
            "product_identity": {
                "main_color": "celadon green",
                "shape": "round tableware set",
            },
        }
    )

    assert result["status"] == "passed"
    assert result["passed"] is True
    assert all(result["checks"].values())


def test_apimart_gemini_validator_treats_shape_mismatch_as_advisory() -> None:
    session = _Session([_chat_payload(PRODUCT_SHAPE_ADVISORY_CONTENT)])
    provider = APIMartGeminiReversePromptProvider(api_key="api-test-key", session=session)

    result = provider.validate_product_fidelity_sync(
        {
            "product_image_url": "https://assets.test/green-celadon.png",
            "rendered_image_url": "https://assets.test/green-celadon-layout-variant.png",
            "product_identity": {
                "main_color": "celadon green",
                "shape": "round plate, bowl, and handled cup",
            },
        }
    )

    assert result["status"] == "passed"
    assert result["passed"] is True
    assert result["checks"] == {
        "main_color_match": True,
        "pattern_match": True,
        "shape_match": False,
    }


@pytest.mark.parametrize(
    ("main_color_match", "pattern_match"),
    [(False, True), (True, False)],
)
def test_apimart_gemini_validator_requires_color_and_pattern_match(
    main_color_match: bool,
    pattern_match: bool,
) -> None:
    result = normalize_product_validation_payload(
        {
            "validation": {
                "status": "passed",
                "main_color_match": main_color_match,
                "pattern_match": pattern_match,
                "shape_match": True,
                "reason": "One blocking identity attribute differs.",
            }
        }
    )

    assert result["status"] == "failed"
    assert result["passed"] is False
    assert result["checks"]["shape_match"] is True


def test_apimart_gemini_provider_uses_token_pricing_when_chat_response_has_no_credits(monkeypatch):
    monkeypatch.setattr(
        "app.services.apimart_costs.settings.engine_apimart_credit_usd",
        Decimal("0.10"),
    )
    monkeypatch.setattr(
        "app.services.apimart_costs.settings.engine_usd_cny_rate",
        Decimal("7.20"),
    )
    session = _Session([_chat_payload(JSON_CONTENT)])
    provider = APIMartGeminiReversePromptProvider(
        api_key="api-test-key",
        base_url="https://api.apimart.ai/v1",
        model="gemini-3.1-pro-preview",
        session=session,
    )

    result = provider.reverse_image_sync({"image_url": "https://assets.test/input.png"})

    assert session.calls[0]["json"]["stream"] is False
    assert result["prompt_zh"].startswith("Premium perfume")
    assert result["target_format"] == "seedance_2_0"
    assert result["prompt_tokens"] == 1000
    assert result["completion_tokens"] == 500
    assert result["credits"] == Decimal("0.064")
    assert result["cost_cents"] == 5


def test_apimart_gemini_provider_parses_streaming_sse_response(monkeypatch):
    monkeypatch.setattr(
        "app.services.apimart_costs.settings.engine_apimart_credit_usd",
        Decimal("0.10"),
    )
    monkeypatch.setattr(
        "app.services.apimart_costs.settings.engine_usd_cny_rate",
        Decimal("7.20"),
    )
    midpoint = len(JSON_CONTENT) // 2
    stream_response = _SseResponse(
        [
            (
                'data: {"choices":[{"delta":{"content":'
                f"{jsonlib.dumps(JSON_CONTENT[:midpoint])}"
                "}}]}"
            ),
            (
                'data: {"choices":[{"delta":{"content":'
                f"{jsonlib.dumps(JSON_CONTENT[midpoint:])}"
                '}}],"usage":{"prompt_tokens":1195,'
                '"completion_tokens":1211,"total_tokens":2406}}'
            ).encode(),
            "data: [DONE]",
        ]
    )
    provider = APIMartGeminiReversePromptProvider(
        api_key="api-test-key",
        session=_Session([stream_response]),
    )

    result = provider.reverse_image_sync({"image_url": "https://assets.test/input.png"})

    assert result["prompt_en"].startswith("Glass perfume")
    assert result["prompt_tokens"] == 1195
    assert result["completion_tokens"] == 1211
    assert result["total_tokens"] == 2406
    assert result["credits"] == Decimal("0.1353760")
    assert result["cost_cents"] == 10


def test_apimart_gemini_provider_uses_reasoning_content_when_message_content_empty():
    provider = APIMartGeminiReversePromptProvider(
        api_key="api-test-key",
        session=_Session(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "reasoning_content": JSON_CONTENT,
                            }
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 500,
                        "total_tokens": 1500,
                    },
                }
            ]
        ),
    )

    result = provider.reverse_image_sync({"image_url": "https://assets.test/input.png"})

    assert result["prompt_zh"].startswith("Premium perfume")
    assert result["completion_tokens"] == 500


def test_apimart_gemini_provider_prefers_authoritative_credits_when_present(monkeypatch):
    monkeypatch.setattr(
        "app.services.apimart_costs.settings.engine_apimart_credit_usd",
        Decimal("0.10"),
    )
    monkeypatch.setattr(
        "app.services.apimart_costs.settings.engine_usd_cny_rate",
        Decimal("7.20"),
    )
    provider = APIMartGeminiReversePromptProvider(
        api_key="api-test-key",
        session=_Session([_chat_payload(JSON_CONTENT, credits="2.5")]),
    )

    result = provider.reverse_image_sync({"image_url": "https://assets.test/input.png"})

    assert result["credits"] == Decimal("2.5")
    assert result["cost_cents"] == 180


def test_apimart_gemini_provider_accepts_apimart_data_wrapper():
    provider = APIMartGeminiReversePromptProvider(
        api_key="api-test-key",
        session=_Session([{"code": 200, "data": _chat_payload(JSON_CONTENT)}]),
    )

    result = provider.reverse_image_sync({"image_url": "https://assets.test/input.png"})

    assert result["prompt_tokens"] == 1000
    assert result["completion_tokens"] == 500
    assert result["prompt_en"].startswith("Glass perfume")


def test_apimart_gemini_provider_rejects_apimart_error_envelope():
    provider = APIMartGeminiReversePromptProvider(
        api_key="api-test-key",
        session=_Session([{"code": 401, "message": "invalid token"}]),
    )

    try:
        provider.reverse_image_sync({"image_url": "https://assets.test/input.png"})
    except Exception as exc:
        assert exc.__class__.__name__ == "APIMartGeminiReversePromptError"
        assert "invalid token" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("APIMart error envelope must fail")


def test_apimart_gemini_provider_retries_once_when_model_returns_invalid_json():
    session = _Session(
        [
            _chat_payload("not-json"),
            _chat_payload(JSON_CONTENT),
        ]
    )
    provider = APIMartGeminiReversePromptProvider(api_key="api-test-key", session=session)

    result = provider.reverse_image_sync({"image_url": "https://assets.test/input.png"})

    assert result["prompt_en"].startswith("Glass perfume")
    assert len(session.calls) == 2
    retry_text_parts = session.calls[1]["json"]["messages"][-1]["content"]
    assert any(
        "Previous response was not valid JSON" in part.get("text", "")
        for part in retry_text_parts
    )


def test_apimart_gemini_provider_retries_once_when_model_returns_empty_content():
    session = _Session(
        [
            _chat_payload(""),
            _chat_payload(JSON_CONTENT),
        ]
    )
    provider = APIMartGeminiReversePromptProvider(api_key="api-test-key", session=session)

    result = provider.reverse_image_sync({"image_url": "https://assets.test/input.png"})

    assert result["prompt_en"].startswith("Glass perfume")
    assert len(session.calls) == 2


def _seed_reverse_prompt_provider(db, tenant_id: str | None = None) -> None:
    db.add_all(
        [
            ProviderConfig(
                tenant_id=tenant_id,
                capability="reverse_prompt",
                provider="apimart-gemini",
                config={"api_key": "api-test-key"},
                is_active=True,
            ),
            CreditRate(
                tenant_id=tenant_id,
                capability="reverse_prompt",
                unit="call",
                credits_per_unit=Decimal("30.0000"),
                is_active=True,
            ),
        ]
    )


def _seed_image_asset(db, tenant_id: str) -> Asset:
    asset = Asset(
        tenant_id=tenant_id,
        type="avatar_image",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/source.png",
        mime_type="image/png",
        size_bytes=1234,
        status="ready",
    )
    db.add(asset)
    db.flush()
    return asset


def _seed_video_asset(db, tenant_id: str, *, duration_ms: int = 24_000) -> Asset:
    asset = Asset(
        tenant_id=tenant_id,
        type="video",
        source="upload",
        storage_key=f"tenants/{tenant_id}/uploads/reverse-source.mp4",
        mime_type="video/mp4",
        size_bytes=12_345,
        duration_ms=duration_ms,
        width=720,
        height=1280,
        status="ready",
        metadata_={
            "purpose": "reverse_prompt",
            "container": "mov,mp4,m4a,3gp,3g2,mj2",
            "video_codec": "h264",
            "audio_codec": "",
        },
    )
    db.add(asset)
    db.flush()
    return asset


def _capture_postgresql_selects(db) -> list[str]:
    statements: list[str] = []

    @event.listens_for(db, "do_orm_execute")
    def _capture_statement(orm_execute_state) -> None:
        if orm_execute_state.is_select:
            statements.append(
                str(orm_execute_state.statement.compile(dialect=postgresql.dialect()))
            )

    return statements


def test_reverse_prompt_video_create_subscription_query_uses_for_update(
    auth_db,
    auth_context,
):
    from app.services.reverse_prompt import create_reverse_prompt_job

    with auth_db() as db:
        asset = _seed_video_asset(db, auth_context["tenant_id"])
        user = db.get(User, auth_context["user_id"])
        statements = _capture_postgresql_selects(db)

        create_reverse_prompt_job(
            db,
            user=user,
            source_asset_id=asset.id,
            target_format="seedance_2_0",
            storage=SimpleNamespace(),
        )

        subscription_queries = [sql for sql in statements if "FROM subscriptions" in sql]
        assert len(subscription_queries) == 1
        assert "FOR UPDATE" in subscription_queries[0]


def test_reverse_prompt_video_regenerate_job_query_uses_for_update(
    auth_db,
    auth_context,
):
    from app.services.reverse_prompt import regenerate_reverse_prompt_job

    with auth_db() as setup:
        asset = _seed_video_asset(setup, auth_context["tenant_id"])
        job = ReversePromptJob(
            tenant_id=auth_context["tenant_id"],
            created_by_user_id=auth_context["user_id"],
            source_kind="video",
            source_asset_id=asset.id,
            source_storage_key=asset.storage_key,
            target_format="seedance_2_0",
            status="succeeded",
        )
        setup.add(job)
        setup.commit()
        job_id = job.id

    with auth_db() as db:
        user = db.get(User, auth_context["user_id"])
        statements = _capture_postgresql_selects(db)
        regenerate_reverse_prompt_job(
            db,
            user=user,
            job_id=job_id,
            storage=SimpleNamespace(),
        )

        decision_queries = [
            sql
            for sql in statements
            if "FROM reverse_prompt_jobs" in sql
            and "AND reverse_prompt_jobs.tenant_id =" in sql
        ]
        assert len(decision_queries) == 1
        assert "FOR UPDATE" in decision_queries[0]


def test_reverse_prompt_video_returns_202_queues_job_and_reserves_fixed_quota(
    auth_db,
    auth_context,
    monkeypatch,
):
    session = auth_db()
    _seed_reverse_prompt_provider(session)
    asset = _seed_video_asset(session, auth_context["tenant_id"])
    subscription = session.scalar(
        select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
    )
    asset_id = asset.id
    initial_used = subscription.quota_credits_used
    initial_reserved = subscription.quota_credits_reserved
    session.commit()
    session.close()

    enqueued: dict[str, object] = {}

    class _FakeTask:
        def apply_async(self, *, args, task_id, queue=None):
            enqueued.update({"args": args, "task_id": task_id, "queue": queue})
            return SimpleNamespace(status="PENDING")

    from app.api.v1.routes import reverse_prompt as reverse_prompt_route

    monkeypatch.setattr(
        reverse_prompt_route,
        "generate_reverse_prompt_video_task",
        _FakeTask(),
        raising=False,
    )

    client = TestClient(app)
    response = client.post(
        "/api/v1/reverse-prompt/jobs",
        json={"source_asset_id": asset_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 202
    body = response.json()["data"]
    assert body["status"] == "queued"
    assert body["source_kind"] == "video"
    assert enqueued == {"args": [body["id"]], "task_id": body["id"], "queue": "image"}

    db = auth_db()
    usage = db.scalar(
        select(UsageRecord).where(
            UsageRecord.tenant_id == auth_context["tenant_id"],
            UsageRecord.capability == "reverse_prompt_video",
        )
    )
    subscription = db.scalar(
        select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
    )
    assert usage is not None
    assert usage.status == "reserved"
    assert usage.unit == "call"
    assert usage.quantity == Decimal("1.000")
    assert usage.credits == Decimal("100.00")
    assert subscription.quota_credits_used == initial_used
    assert subscription.quota_credits_reserved == initial_reserved + 100
    db.close()


def test_reverse_prompt_video_two_sessions_allow_only_one_create_when_balance_fits_once(
    auth_db,
    auth_context,
):
    setup = auth_db()
    _seed_reverse_prompt_provider(setup)
    asset = _seed_video_asset(setup, auth_context["tenant_id"])
    subscription = setup.scalar(
        select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
    )
    subscription.quota_credits_total = 100
    subscription.quota_credits_used = 0
    subscription.quota_credits_reserved = 0
    asset_id = asset.id
    subscription_id = subscription.id
    setup.commit()
    setup.close()

    from app.services.reverse_prompt import create_reverse_prompt_job

    first = auth_db()
    second = auth_db()
    try:
        first_subscription = first.get(Subscription, subscription_id)
        second_subscription = second.get(Subscription, subscription_id)
        first_user = first.get(User, auth_context["user_id"])
        second_user = second.get(User, auth_context["user_id"])
        assert first_subscription.quota_credits_reserved == 0
        assert second_subscription.quota_credits_reserved == 0

        created = create_reverse_prompt_job(
            first,
            user=first_user,
            source_asset_id=asset_id,
            target_format="seedance_2_0",
            storage=SimpleNamespace(),
        )
        assert created.status == "queued"
        assert second_subscription.quota_credits_reserved == 0

        with pytest.raises(AppError) as exc_info:
            create_reverse_prompt_job(
                second,
                user=second_user,
                source_asset_id=asset_id,
                target_format="seedance_2_0",
                storage=SimpleNamespace(),
            )
        assert exc_info.value.code == "TENANT_QUOTA_EXCEEDED"
        second.rollback()
    finally:
        first.close()
        second.close()

    with auth_db() as db:
        jobs = list(
            db.scalars(
                select(ReversePromptJob).where(
                    ReversePromptJob.tenant_id == auth_context["tenant_id"],
                    ReversePromptJob.source_asset_id == asset_id,
                )
            )
        )
        reserved_records = list(
            db.scalars(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == auth_context["tenant_id"],
                    UsageRecord.capability == "reverse_prompt_video",
                    UsageRecord.status == "reserved",
                )
            )
        )
        subscription = db.get(Subscription, subscription_id)
        assert len(jobs) == 1
        assert len(reserved_records) == 1
        assert subscription.quota_credits_reserved == sum(
            int(record.credits) for record in reserved_records
        )


def test_reverse_prompt_video_queue_failure_releases_reserved_quota(
    auth_db,
    auth_context,
    monkeypatch,
):
    session = auth_db()
    _seed_reverse_prompt_provider(session)
    asset = _seed_video_asset(session, auth_context["tenant_id"])
    subscription = session.scalar(
        select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
    )
    asset_id = asset.id
    initial_used = subscription.quota_credits_used
    initial_reserved = subscription.quota_credits_reserved
    session.commit()
    session.close()

    class _FailingTask:
        def apply_async(self, *, args, task_id, queue=None):
            raise RuntimeError("broker unavailable")

    from app.api.v1.routes import reverse_prompt as reverse_prompt_route

    monkeypatch.setattr(
        reverse_prompt_route,
        "generate_reverse_prompt_video_task",
        _FailingTask(),
    )

    client = TestClient(app)
    response = client.post(
        "/api/v1/reverse-prompt/jobs",
        json={"source_asset_id": asset_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REVERSE_PROMPT_QUEUE_FAILED"

    db = auth_db()
    job = db.scalar(
        select(ReversePromptJob).where(
            ReversePromptJob.tenant_id == auth_context["tenant_id"],
            ReversePromptJob.source_asset_id == asset_id,
        )
    )
    usage = db.scalar(
        select(UsageRecord).where(UsageRecord.reverse_prompt_job_id == job.id)
    )
    subscription = db.scalar(
        select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
    )
    assert job.status == "failed"
    assert job.error_code == "REVERSE_PROMPT_QUEUE_FAILED"
    assert usage.status == "released"
    assert subscription.quota_credits_used == initial_used
    assert subscription.quota_credits_reserved == initial_reserved
    db.close()


def test_reverse_prompt_video_worker_succeeds_settles_once_and_is_pollable(
    auth_db,
    auth_context,
    monkeypatch,
):
    session = auth_db()
    _seed_reverse_prompt_provider(session)
    asset = _seed_video_asset(session, auth_context["tenant_id"])
    asset_id = asset.id
    storage_key = asset.storage_key
    session.commit()
    session.close()

    class _FakeTask:
        def apply_async(self, *, args, task_id, queue=None):
            return SimpleNamespace(status="PENDING")

    from app.api.v1.routes import reverse_prompt as reverse_prompt_route

    monkeypatch.setattr(
        reverse_prompt_route,
        "generate_reverse_prompt_video_task",
        _FakeTask(),
    )
    client = TestClient(app)
    created = client.post(
        "/api/v1/reverse-prompt/jobs",
        json={"source_asset_id": asset_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )
    assert created.status_code == 202
    job_id = created.json()["data"]["id"]

    from app.services import reverse_prompt_video

    class _Storage:
        def get_bytes(self, key: str) -> bytes:
            assert key == storage_key
            return b"fake-video-content"

    extraction_calls: list[tuple[Path, float]] = []

    def fake_extract(path: Path, *, duration_sec: float, run=None) -> list[bytes]:
        assert path.exists()
        extraction_calls.append((path, duration_sec))
        return [f"jpeg-{index}".encode() for index in range(8)]

    provider_calls: list[dict[str, object]] = []
    provider_result = {
        **jsonlib.loads(VIDEO_JSON_CONTENT),
        "provider": "apimart",
        "model": "gemini-3.1-pro-preview",
        "prompt_tokens": 2000,
        "completion_tokens": 800,
        "total_tokens": 2800,
        "credits": Decimal("0.1088"),
        "cost_cents": 8,
        "raw_model_json": jsonlib.loads(VIDEO_JSON_CONTENT),
    }
    provider_result["video_analysis"] = {
        **provider_result["video_analysis"],
        "duration_sec": 24.0,
        "audio_transcript": None,
        "bgm_style": None,
    }
    def fake_reverse_video_frames(payload):
        provider_calls.append(dict(payload))
        with auth_db() as running_db:
            assert running_db.get(ReversePromptJob, job_id).status == "running"
        return provider_result

    fake_provider = SimpleNamespace(reverse_video_frames=fake_reverse_video_frames)
    monkeypatch.setattr(
        reverse_prompt_video,
        "create_object_storage",
        lambda _settings: _Storage(),
    )
    monkeypatch.setattr(
        reverse_prompt_video,
        "extract_uniform_video_frames",
        fake_extract,
    )
    monkeypatch.setattr(
        reverse_prompt_video,
        "resolve",
        lambda *args, **kwargs: fake_provider,
    )

    result = reverse_prompt_video.run_reverse_prompt_video_job(
        job_id,
        session_factory=auth_db,
    )
    repeated = reverse_prompt_video.run_reverse_prompt_video_job(
        job_id,
        session_factory=auth_db,
    )

    assert result == {"job_id": job_id, "status": "succeeded"}
    assert repeated == {"job_id": job_id, "status": "succeeded"}
    assert len(extraction_calls) == 1
    assert not extraction_calls[0][0].exists()
    assert len(provider_calls) == 1
    assert provider_calls[0]["duration_sec"] == 24.0
    assert provider_calls[0]["timestamps_sec"] == frame_timestamps(24.0)
    assert len(provider_calls[0]["image_urls"]) == 8
    assert all(
        value.startswith("data:image/jpeg;base64,")
        for value in provider_calls[0]["image_urls"]
    )

    polled = client.get(
        f"/api/v1/reverse-prompt/jobs/{job_id}",
        headers=auth_context["headers"],
    )
    assert polled.status_code == 200
    body = polled.json()["data"]
    assert body["status"] == "succeeded"
    assert body["result"]["video_analysis"]["duration_sec"] == 24.0
    assert body["result"]["video_analysis"]["audio_transcript"] is None
    assert body["result"]["video_analysis"]["bgm_style"] is None

    db = auth_db()
    usage_records = list(
        db.scalars(
            select(UsageRecord).where(UsageRecord.reverse_prompt_job_id == job_id)
        )
    )
    subscription = db.scalar(
        select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
    )
    assert len(usage_records) == 1
    assert usage_records[0].status == "settled"
    assert usage_records[0].unit == "token"
    assert usage_records[0].quantity == Decimal("2800.000")
    assert usage_records[0].credits == Decimal("100.00")
    assert usage_records[0].cost_cents == 8
    assert subscription.quota_credits_reserved == 0
    assert subscription.quota_credits_used == 100
    db.close()


def test_reverse_prompt_video_job_can_be_claimed_only_once(
    auth_db,
    auth_context,
    monkeypatch,
):
    session = auth_db()
    _seed_reverse_prompt_provider(session)
    asset = _seed_video_asset(session, auth_context["tenant_id"])
    asset_id = asset.id
    session.commit()
    session.close()

    class _FakeTask:
        def apply_async(self, *, args, task_id, queue=None):
            return SimpleNamespace(status="PENDING")

    from app.api.v1.routes import reverse_prompt as reverse_prompt_route

    monkeypatch.setattr(
        reverse_prompt_route,
        "generate_reverse_prompt_video_task",
        _FakeTask(),
    )
    client = TestClient(app)
    created = client.post(
        "/api/v1/reverse-prompt/jobs",
        json={"source_asset_id": asset_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )
    job_id = created.json()["data"]["id"]

    from app.services.reverse_prompt_video import claim_reverse_prompt_video_job

    first = auth_db()
    second = auth_db()
    try:
        assert claim_reverse_prompt_video_job(first, job_id=job_id) is True
        assert claim_reverse_prompt_video_job(second, job_id=job_id) is False
    finally:
        first.close()
        second.close()

    with auth_db() as db:
        assert db.get(ReversePromptJob, job_id).status == "running"
        usages = list(
            db.scalars(
                select(UsageRecord).where(UsageRecord.reverse_prompt_job_id == job_id)
            )
        )
        assert len(usages) == 1
        assert usages[0].status == "reserved"


def test_reverse_prompt_video_regenerate_returns_202_and_reserves_one_new_call(
    auth_db,
    auth_context,
    monkeypatch,
):
    session = auth_db()
    _seed_reverse_prompt_provider(session)
    asset = _seed_video_asset(session, auth_context["tenant_id"])
    asset_id = asset.id
    session.commit()
    session.close()

    enqueued: list[dict[str, object]] = []

    class _FakeTask:
        def apply_async(self, *, args, task_id, queue=None):
            enqueued.append({"args": args, "task_id": task_id, "queue": queue})
            return SimpleNamespace(status="PENDING")

    from app.api.v1.routes import reverse_prompt as reverse_prompt_route

    monkeypatch.setattr(
        reverse_prompt_route,
        "generate_reverse_prompt_video_task",
        _FakeTask(),
    )
    client = TestClient(app)
    created = client.post(
        "/api/v1/reverse-prompt/jobs",
        json={"source_asset_id": asset_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )
    assert created.status_code == 202
    job_id = created.json()["data"]["id"]

    from app.services.quota import settle_reverse_prompt_video_quota

    db = auth_db()
    job = db.get(ReversePromptJob, job_id)
    settle_reverse_prompt_video_quota(
        db,
        tenant_id=auth_context["tenant_id"],
        reverse_prompt_job_id=job_id,
        provider="apimart",
        model="gemini-3.1-pro-preview",
        total_tokens=2500,
        cost_cents=7,
    )
    job.status = "succeeded"
    job.result_json = {
        "target_format": "seedance_2_0",
        "prompt_zh": "old",
        "prompt_en": "old",
        "fill_targets": {},
    }
    db.commit()
    db.close()

    regenerated = client.post(
        f"/api/v1/reverse-prompt/jobs/{job_id}/regenerate",
        headers=auth_context["headers"],
    )
    duplicate = client.post(
        f"/api/v1/reverse-prompt/jobs/{job_id}/regenerate",
        headers=auth_context["headers"],
    )

    assert regenerated.status_code == 202
    assert regenerated.json()["data"]["status"] == "queued"
    assert regenerated.json()["data"]["result"] is None
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "REVERSE_PROMPT_ALREADY_RUNNING"
    assert enqueued == [
        {"args": [job_id], "task_id": job_id, "queue": "image"},
        {"args": [job_id], "task_id": job_id, "queue": "image"},
    ]

    db = auth_db()
    usages = list(
        db.scalars(
            select(UsageRecord)
            .where(UsageRecord.reverse_prompt_job_id == job_id)
            .order_by(UsageRecord.created_at)
        )
    )
    subscription = db.scalar(
        select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
    )
    assert [usage.status for usage in usages] == ["settled", "reserved"]
    assert subscription.quota_credits_used == 100
    assert subscription.quota_credits_reserved == 100
    db.close()


def test_reverse_prompt_video_two_sessions_grant_only_one_regenerate(
    auth_db,
    auth_context,
):
    from app.services.quota import settle_reverse_prompt_video_quota
    from app.services.reverse_prompt import (
        create_reverse_prompt_job,
        regenerate_reverse_prompt_job,
    )

    setup = auth_db()
    _seed_reverse_prompt_provider(setup)
    asset = _seed_video_asset(setup, auth_context["tenant_id"])
    user = setup.get(User, auth_context["user_id"])
    job = create_reverse_prompt_job(
        setup,
        user=user,
        source_asset_id=asset.id,
        target_format="seedance_2_0",
        storage=SimpleNamespace(),
    )
    settle_reverse_prompt_video_quota(
        setup,
        tenant_id=auth_context["tenant_id"],
        reverse_prompt_job_id=job.id,
        provider="apimart",
        model="gemini-3.1-pro-preview",
        total_tokens=2500,
        cost_cents=7,
    )
    job.status = "succeeded"
    job.result_json = {
        "target_format": "seedance_2_0",
        "prompt_zh": "old",
        "prompt_en": "old",
        "fill_targets": {},
    }
    job_id = job.id
    subscription_id = setup.scalar(
        select(Subscription.id).where(
            Subscription.tenant_id == auth_context["tenant_id"]
        )
    )
    setup.commit()
    setup.close()

    first = auth_db()
    second = auth_db()
    try:
        first_job = first.get(ReversePromptJob, job_id)
        second_job = second.get(ReversePromptJob, job_id)
        first_user = first.get(User, auth_context["user_id"])
        second_user = second.get(User, auth_context["user_id"])
        assert first_job.status == "succeeded"
        assert second_job.status == "succeeded"

        regenerated = regenerate_reverse_prompt_job(
            first,
            user=first_user,
            job_id=job_id,
            storage=SimpleNamespace(),
        )
        assert regenerated.status == "queued"
        assert second_job.status == "succeeded"

        with pytest.raises(AppError) as exc_info:
            regenerate_reverse_prompt_job(
                second,
                user=second_user,
                job_id=job_id,
                storage=SimpleNamespace(),
            )
        assert exc_info.value.code == "REVERSE_PROMPT_ALREADY_RUNNING"
        second.rollback()
    finally:
        first.close()
        second.close()

    with auth_db() as db:
        usages = list(
            db.scalars(
                select(UsageRecord)
                .where(UsageRecord.reverse_prompt_job_id == job_id)
                .order_by(UsageRecord.created_at)
            )
        )
        reserved_records = [usage for usage in usages if usage.status == "reserved"]
        subscription = db.get(Subscription, subscription_id)
        assert [usage.status for usage in usages] == ["settled", "reserved"]
        assert len(reserved_records) == 1
        assert subscription.quota_credits_reserved == sum(
            int(record.credits) for record in reserved_records
        )


def test_reverse_prompt_video_worker_invalidated_source_fails_and_releases_quota(
    auth_db,
    auth_context,
    monkeypatch,
):
    session = auth_db()
    _seed_reverse_prompt_provider(session)
    asset = _seed_video_asset(session, auth_context["tenant_id"])
    asset_id = asset.id
    session.commit()
    session.close()

    class _FakeTask:
        def apply_async(self, *, args, task_id, queue=None):
            return SimpleNamespace(status="PENDING")

    from app.api.v1.routes import reverse_prompt as reverse_prompt_route

    monkeypatch.setattr(
        reverse_prompt_route,
        "generate_reverse_prompt_video_task",
        _FakeTask(),
    )
    client = TestClient(app)
    created = client.post(
        "/api/v1/reverse-prompt/jobs",
        json={"source_asset_id": asset_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )
    assert created.status_code == 202
    job_id = created.json()["data"]["id"]

    db = auth_db()
    db.get(Asset, asset_id).status = "failed"
    db.commit()
    db.close()

    from app.services import reverse_prompt_video

    monkeypatch.setattr(
        reverse_prompt_video,
        "create_object_storage",
        lambda _settings: SimpleNamespace(),
    )
    with pytest.raises(
        reverse_prompt_video.ReversePromptVideoProcessingError,
        match="processing failed",
    ):
        reverse_prompt_video.run_reverse_prompt_video_job(
            job_id,
            session_factory=auth_db,
        )

    db = auth_db()
    job = db.get(ReversePromptJob, job_id)
    usage = db.scalar(
        select(UsageRecord).where(UsageRecord.reverse_prompt_job_id == job_id)
    )
    subscription = db.scalar(
        select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
    )
    assert job.status == "failed"
    assert job.error_code == "REVERSE_PROMPT_FAILED"
    assert usage.status == "released"
    assert subscription.quota_credits_reserved == 0
    assert subscription.quota_credits_used == 0
    db.close()


def test_reverse_prompt_sync_creates_job_records_usage_and_returns_fill_targets(
    auth_db,
    auth_context,
    monkeypatch,
):
    session = auth_db()
    _seed_reverse_prompt_provider(session)
    asset = _seed_image_asset(session, auth_context["tenant_id"])
    asset_id = asset.id
    asset_storage_key = asset.storage_key
    session.commit()
    session.close()

    class _Storage:
        def presign_get_url(
            self,
            key: str,
            *,
            expires_in: int,
            download_filename: str | None = None,
        ) -> str:
            assert key == asset_storage_key
            return "https://assets.test/presigned-source.png"

    fake_provider = SimpleNamespace(
        reverse_image=lambda payload: {
            "target_format": "seedance_2_0",
            "prompt_zh": "Premium perfume bottle on marble.",
            "prompt_en": (
                "Premium perfume bottle commercial product shot, "
                "soft light, marble surface."
            ),
            "negative_prompt": "blurry, low quality",
            "style_tags": ["commercial", "premium"],
            "camera": "close-up",
            "lighting": "soft light",
            "composition": "centered",
            "subject": "perfume bottle",
            "scene": "marble surface",
            "motion_hint": "slow push-in",
            "selling_points": ["premium texture"],
            "text_in_media": ["PARFUM"],
            "disclaimer": "Verify visible text before reuse.",
            "confidence": 0.91,
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
            "credits": Decimal("0.064"),
            "cost_cents": 5,
            "provider": "apimart",
            "model": "gemini-3.1-pro-preview",
            "raw_model_json": {"prompt_zh": "Premium perfume bottle on marble."},
        }
    )
    monkeypatch.setattr("app.api.v1.routes.reverse_prompt.get_object_storage", lambda: _Storage())
    monkeypatch.setattr(
        "app.services.reverse_prompt.resolve",
        lambda *args, **kwargs: fake_provider,
    )

    client = TestClient(app)
    response = client.post(
        "/api/v1/reverse-prompt",
        json={"source_asset_id": asset_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["status"] == "succeeded"
    assert body["result"]["selling_points"] == ["premium texture"]
    assert body["result"]["text_in_media"] == ["PARFUM"]
    assert body["result"]["disclaimer"] == "Verify visible text before reuse."
    assert body["result"]["fill_targets"]["video_gen"]["prompt"] == body["result"]["prompt_en"]
    assert body["result"]["fill_targets"]["seedance_i2v"]["scene_prompt"]
    assert body["result"]["fill_targets"]["avatar_talk"]["topic"]
    assert body["result"]["video_analysis"] is None
    assert body["result"]["fill_targets"]["ecom_model"]["extra_prompt"]

    db = auth_db()
    job = db.get(ReversePromptJob, body["id"])
    subscription = db.scalar(
        select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
    )
    assert job is not None
    assert job.tenant_id == auth_context["tenant_id"]
    usage = db.scalar(
        select(UsageRecord).where(
            UsageRecord.tenant_id == auth_context["tenant_id"],
            UsageRecord.capability == "reverse_prompt",
        )
    )
    assert usage is not None
    assert usage.video_task_id is None
    assert usage.subscription_id == subscription.id
    assert usage.capability == "reverse_prompt"
    assert usage.provider == "apimart"
    assert usage.model == "gemini-3.1-pro-preview"
    assert usage.unit == "token"
    assert usage.quantity == Decimal("1500.000")
    assert usage.credits == Decimal("30.00")
    assert usage.cost_cents == 5
    assert subscription.quota_credits_used == 30
    db.close()


@pytest.mark.parametrize("placeholder", ["None", "N/A"])
def test_reverse_prompt_sync_stores_empty_disclaimer_for_placeholder_text(
    auth_db,
    auth_context,
    monkeypatch,
    placeholder,
):
    session = auth_db()
    _seed_reverse_prompt_provider(session)
    asset = _seed_image_asset(session, auth_context["tenant_id"])
    asset_id = asset.id
    asset_storage_key = asset.storage_key
    session.commit()
    session.close()

    class _Storage:
        def presign_get_url(
            self,
            key: str,
            *,
            expires_in: int,
            download_filename: str | None = None,
        ) -> str:
            assert key == asset_storage_key
            return "https://assets.test/presigned-source.png"

    fake_provider = SimpleNamespace(
        reverse_image=lambda payload: {
            "target_format": "seedance_2_0",
            "prompt_zh": "Premium perfume bottle on marble.",
            "prompt_en": "Premium perfume bottle commercial product shot.",
            "negative_prompt": "blurry",
            "style_tags": ["commercial"],
            "camera": "close-up",
            "lighting": "soft light",
            "composition": "centered",
            "subject": "perfume bottle",
            "scene": "marble surface",
            "motion_hint": "slow push-in",
            "selling_points": ["premium texture"],
            "text_in_media": [],
            "disclaimer": placeholder,
            "confidence": 0.91,
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
            "credits": Decimal("0.064"),
            "cost_cents": 5,
            "provider": "apimart",
            "model": "gemini-3.1-pro-preview",
        }
    )
    monkeypatch.setattr("app.api.v1.routes.reverse_prompt.get_object_storage", lambda: _Storage())
    monkeypatch.setattr(
        "app.services.reverse_prompt.resolve",
        lambda *args, **kwargs: fake_provider,
    )

    client = TestClient(app)
    response = client.post(
        "/api/v1/reverse-prompt",
        json={"source_asset_id": asset_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["result"]["disclaimer"] == ""
    db = auth_db()
    job = db.get(ReversePromptJob, body["id"])
    assert job.result_json["disclaimer"] == ""
    db.close()


def test_reverse_prompt_rejects_when_quota_is_exhausted(auth_db, auth_context, monkeypatch):
    session = auth_db()
    _seed_reverse_prompt_provider(session)
    asset = _seed_image_asset(session, auth_context["tenant_id"])
    asset_id = asset.id
    subscription = session.scalar(
        select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
    )
    subscription.quota_credits_used = subscription.quota_credits_total
    session.commit()
    session.close()

    fake_provider = SimpleNamespace(
        reverse_image=lambda payload: (_ for _ in ()).throw(
            AssertionError("insufficient quota must not call provider")
        )
    )
    monkeypatch.setattr(
        "app.services.reverse_prompt.resolve",
        lambda *args, **kwargs: fake_provider,
    )

    client = TestClient(app)
    response = client.post(
        "/api/v1/reverse-prompt",
        json={"source_asset_id": asset_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TENANT_QUOTA_EXCEEDED"


def test_reverse_prompt_video_quota_exhaustion_creates_no_job_or_reservation(
    auth_db,
    auth_context,
    monkeypatch,
):
    session = auth_db()
    _seed_reverse_prompt_provider(session)
    asset = _seed_video_asset(session, auth_context["tenant_id"])
    asset_id = asset.id
    subscription = session.scalar(
        select(Subscription).where(Subscription.tenant_id == auth_context["tenant_id"])
    )
    subscription.quota_credits_used = subscription.quota_credits_total
    session.commit()
    session.close()

    class _UnexpectedTask:
        def apply_async(self, **kwargs):
            raise AssertionError("quota rejection must not enqueue a task")

    from app.api.v1.routes import reverse_prompt as reverse_prompt_route

    monkeypatch.setattr(
        reverse_prompt_route,
        "generate_reverse_prompt_video_task",
        _UnexpectedTask(),
    )
    client = TestClient(app)
    response = client.post(
        "/api/v1/reverse-prompt/jobs",
        json={"source_asset_id": asset_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TENANT_QUOTA_EXCEEDED"
    with auth_db() as db:
        assert db.scalar(
            select(ReversePromptJob).where(
                ReversePromptJob.tenant_id == auth_context["tenant_id"]
            )
        ) is None
        assert db.scalar(
            select(UsageRecord).where(
                UsageRecord.tenant_id == auth_context["tenant_id"],
                UsageRecord.capability == "reverse_prompt_video",
            )
        ) is None


def test_reverse_prompt_regenerate_failure_marks_job_failed(auth_db, auth_context, monkeypatch):
    session = auth_db()
    _seed_reverse_prompt_provider(session)
    asset = _seed_image_asset(session, auth_context["tenant_id"])
    job = ReversePromptJob(
        tenant_id=auth_context["tenant_id"],
        created_by_user_id=auth_context["user_id"],
        source_kind="image",
        source_asset_id=asset.id,
        source_storage_key=asset.storage_key,
        target_format="seedance_2_0",
        status="succeeded",
        result_json={"target_format": "seedance_2_0"},
    )
    session.add(job)
    session.commit()
    job_id = job.id
    session.close()

    class _Storage:
        def presign_get_url(
            self,
            key: str,
            *,
            expires_in: int,
            download_filename: str | None = None,
        ) -> str:
            return "https://assets.test/presigned-source.png"

    fake_provider = SimpleNamespace(
        reverse_image=lambda payload: (_ for _ in ()).throw(RuntimeError("provider down"))
    )
    monkeypatch.setattr("app.api.v1.routes.reverse_prompt.get_object_storage", lambda: _Storage())
    monkeypatch.setattr(
        "app.services.reverse_prompt.resolve",
        lambda *args, **kwargs: fake_provider,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/reverse-prompt/jobs/{job_id}/regenerate",
        headers=auth_context["headers"],
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "REVERSE_PROMPT_FAILED"
    db = auth_db()
    failed_job = db.get(ReversePromptJob, job_id)
    assert failed_job.status == "failed"
    assert failed_job.error_code == "REVERSE_PROMPT_FAILED"
    db.close()


def test_reverse_prompt_rejects_cross_tenant_asset(auth_db, auth_context):
    db = auth_db()
    _seed_reverse_prompt_provider(db)
    db.add(Tenant(id="tenant-other", slug="tenant-other", name="Tenant Other"))
    db.flush()
    other = _seed_image_asset(db, "tenant-other")
    other_id = other.id
    other_video = _seed_video_asset(db, "tenant-other")
    other_video_id = other_video.id
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.post(
        "/api/v1/reverse-prompt",
        json={"source_asset_id": other_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )
    video_response = client.post(
        "/api/v1/reverse-prompt/jobs",
        json={"source_asset_id": other_video_id, "target_format": "seedance_2_0"},
        headers=auth_context["headers"],
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REVERSE_PROMPT_SOURCE_NOT_FOUND"
    assert video_response.status_code == 404
    assert video_response.json()["error"]["code"] == "REVERSE_PROMPT_SOURCE_NOT_FOUND"


def test_reverse_prompt_rejects_link_input_without_creating_job(auth_db, auth_context):
    db = auth_db()
    _seed_reverse_prompt_provider(db)
    asset = _seed_image_asset(db, auth_context["tenant_id"])
    asset_id = asset.id
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.post(
        "/api/v1/reverse-prompt/jobs",
        json={
            "source_asset_id": asset_id,
            "target_format": "seedance_2_0",
            "source_url": "https://example.test/video.mp4",
        },
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
    with auth_db() as db:
        assert db.scalar(
            select(ReversePromptJob).where(
                ReversePromptJob.tenant_id == auth_context["tenant_id"]
            )
        ) is None
