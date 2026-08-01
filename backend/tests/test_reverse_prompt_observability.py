from __future__ import annotations

import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.db.models import Asset, ReversePromptJob, Subscription, UsageRecord, User


class _CaptureLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, event: str, **fields: Any) -> None:
        self.records.append(("info", event, fields))

    def warning(self, event: str, **fields: Any) -> None:
        self.records.append(("warning", event, fields))

    def error(self, event: str, **fields: Any) -> None:
        self.records.append(("error", event, fields))

    def exception(self, event: str, **fields: Any) -> None:
        self.records.append(("error", event, fields))


@pytest.fixture(scope="module")
def oversized_proxy_bytes(tmp_path_factory) -> bytes:
    proxy_path = tmp_path_factory.mktemp("reverse-prompt-observability") / "oversized.mp4"
    result = subprocess.run(
        [
            os.environ.get("FFMPEG_BINARY", "ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=1280x720:r=2:d=1",
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
            str(proxy_path),
        ],
        capture_output=True,
        check=False,
        timeout=30.0,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return proxy_path.read_bytes()


_UPSTREAM_REQUIRED_FIELDS = {
    "job_id",
    "tenant_id",
    "call_index",
    "stage",
    "parent_stage",
    "operation",
    "provider",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "cache_tokens_reported",
    "input_modality_tokens_reported",
    "output_modality_tokens_reported",
    "input_text_tokens",
    "input_image_tokens",
    "input_video_tokens",
    "input_audio_tokens",
    "output_text_tokens",
    "output_image_tokens",
    "output_video_tokens",
    "output_audio_tokens",
    "candidate_tokens",
    "thought_tokens",
    "credits",
    "cost_cents",
    "cost_source",
    "input_credits_per_m",
    "output_credits_per_m",
    "cached_input_credits_per_m",
    "token_rate_source",
    "apimart_credit_usd",
    "usd_cny_rate",
    "cost_estimate_uncertain",
    "elapsed_ms",
    "outcome",
    "error_type",
    "media_bytes",
    "frame_count",
    "media_duration_sec",
    "video_tokens_per_second",
    "segment_index",
    "call_attempt",
    "upstream_attempt",
}


def test_production_code_has_no_legacy_token_rate_consumer() -> None:
    app_root = Path(__file__).parents[1] / "app"
    forbidden_sources = (
        "legacy_" "settings",
        "_TOKEN_CREDITS_PER_M_BY_MODEL",
    )
    offenders = [
        (str(path.relative_to(app_root)), forbidden_source)
        for path in app_root.rglob("*.py")
        for forbidden_source in forbidden_sources
        if forbidden_source in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
    compatibility_fields = (
        "engine_apimart_reverse_prompt_input_credits_per_m",
        "engine_apimart_reverse_prompt_output_credits_per_m",
    )
    for compatibility_field in compatibility_fields:
        references = [
            path.relative_to(app_root).as_posix()
            for path in app_root.rglob("*.py")
            if compatibility_field in path.read_text(encoding="utf-8")
        ]
        assert references == ["core/config.py"]


def _usage(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_cents: int,
    credits: str,
    cached_tokens: int = 0,
    input_text_tokens: int = 0,
    input_image_tokens: int = 0,
    input_video_tokens: int = 0,
    input_audio_tokens: int = 0,
    output_text_tokens: int = 0,
    output_image_tokens: int = 0,
) -> dict[str, Any]:
    return {
        "provider": "apimart",
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cached_tokens": cached_tokens,
        "input_modality_tokens_reported": True,
        "output_modality_tokens_reported": True,
        "input_text_tokens": input_text_tokens,
        "input_image_tokens": input_image_tokens,
        "input_video_tokens": input_video_tokens,
        "input_audio_tokens": input_audio_tokens,
        "output_text_tokens": output_text_tokens,
        "output_image_tokens": output_image_tokens,
        "output_audio_tokens": 0,
        "candidate_tokens": output_text_tokens,
        "thought_tokens": max(0, completion_tokens - output_text_tokens),
        "credits": Decimal(credits),
        "cost_cents": cost_cents,
        "cost_source": "provider_credits",
    }


def _segment_result(payload: dict[str, Any]) -> dict[str, Any]:
    index = int(payload["segment_index"])
    start = float(payload["segment_start_sec"])
    end = float(payload["segment_end_sec"])
    usage = (
        _usage(
            model="gemini-3.6-flash",
            prompt_tokens=3_000,
            completion_tokens=120,
            cost_cents=3,
            credits="0.04",
            cached_tokens=500,
            input_text_tokens=500,
            input_video_tokens=2_000,
            output_text_tokens=100,
        )
        if index == 1
        else _usage(
            model="gemini-3.6-flash",
            prompt_tokens=1_500,
            completion_tokens=80,
            cost_cents=3,
            credits="0.04",
            cached_tokens=200,
            input_text_tokens=300,
            input_video_tokens=1_000,
            output_text_tokens=70,
        )
    )
    return {
        "segment_analysis": {
            "segment_index": index,
            "segment_start_sec": start,
            "segment_end_sec": end,
            "subject": f"subject-{index}",
            "scene": f"scene-{index}",
            "composition": "portrait",
            "camera": "orbit",
            "lighting": "soft",
            "motion": "steady",
            "style": "commercial",
            "visible_text": [],
            "shot_list": [
                {
                    "index": index - 1,
                    "start_sec": start,
                    "end_sec": end,
                    "visual": f"segment-{index}",
                    "camera": "orbit",
                    "motion": "steady",
                    "transition": "cut",
                }
            ],
            "segment_summary": f"segment-{index}",
        },
        **usage,
        "raw_model_json": {"segment": index},
    }


def _summary_result(payload: dict[str, Any]) -> dict[str, Any]:
    shots = [
        dict(segment["shot_list"][0])
        for segment in payload["segment_analyses"]
    ]
    return {
        "target_format": "seedance_2_0",
        "prompt_zh": "完整商品视频。",
        "prompt_en": "Complete product video.",
        "negative_prompt": "wrong product",
        "style_tags": ["commercial"],
        "camera": "orbit",
        "lighting": "soft",
        "composition": "portrait",
        "subject": "product",
        "scene": "studio",
        "motion_hint": "steady",
        "selling_points": ["detail"],
        "text_in_media": [],
        "disclaimer": "",
        "confidence": 0.9,
        "video_analysis": {
            "duration_sec": 75.0,
            "pacing": "medium",
            "shot_list": shots,
            "audio_transcript": payload["audio_transcript"],
            "bgm_style": None,
            "shot_summary": "two segments",
        },
        **_usage(
            model="gemini-3.6-flash",
            prompt_tokens=500,
            completion_tokens=200,
            cost_cents=2,
            credits="0.03",
            input_text_tokens=500,
            output_text_tokens=180,
        ),
        "raw_model_json": {"summary": True},
    }


def test_long_video_full_flow_emits_reconstructable_logs_without_changing_billing(
    auth_db,
    auth_context,
    monkeypatch,
) -> None:
    from app.core.config import settings
    from app.services import reverse_prompt_video
    from app.services.reverse_prompt import create_reverse_prompt_job

    transcript = "这是不应出现在日志里的完整转写。"
    with auth_db() as setup:
        asset = Asset(
            tenant_id=auth_context["tenant_id"],
            type="video",
            source="upload",
            storage_key=(
                f"tenants/{auth_context['tenant_id']}/uploads/observability.mp4"
            ),
            mime_type="video/mp4",
            size_bytes=12_345,
            duration_ms=75_000,
            width=720,
            height=1280,
            status="ready",
            metadata_={"purpose": "reverse_prompt", "video_codec": "h264"},
        )
        setup.add(asset)
        setup.flush()
        user = setup.get(User, auth_context["user_id"])
        job = create_reverse_prompt_job(
            setup,
            user=user,
            source_asset_id=asset.id,
            target_format="seedance_2_0",
            storage=SimpleNamespace(),
        )
        job_id = job.id
        storage_key = asset.storage_key

    class _Storage:
        def get_bytes(self, key: str) -> bytes:
            assert key == storage_key
            return b"source-video-bytes"

    provider = SimpleNamespace(
        transcribe_audio=lambda _payload: {
            "audio_transcript": transcript,
            **_usage(
                model="gpt-4o-mini-transcribe",
                prompt_tokens=120,
                completion_tokens=20,
                cost_cents=1,
                credits="0.01",
                input_audio_tokens=120,
                output_text_tokens=20,
            ),
        },
        analyze_video_native_segment=_segment_result,
        summarize_video_segments=_summary_result,
    )
    captured = _CaptureLogger()

    monkeypatch.setattr(
        settings,
        "engine_reverse_prompt_video_analysis_mode",
        "native",
    )
    monkeypatch.setattr(
        settings,
        "engine_reverse_prompt_video_native_segment_seconds",
        60,
    )
    monkeypatch.setattr(
        reverse_prompt_video,
        "create_object_storage",
        lambda _settings: _Storage(),
    )
    monkeypatch.setattr(
        reverse_prompt_video,
        "detect_video_scene_cuts",
        lambda *args, **kwargs: [60.0],
    )
    monkeypatch.setattr(
        reverse_prompt_video,
        "build_video_analysis_proxy",
        lambda *args, **kwargs: b"proxy-video",
    )
    monkeypatch.setattr(
        reverse_prompt_video,
        "_probe_video_dimensions",
        lambda _video_bytes: (360, 640),
    )
    monkeypatch.setattr(
        reverse_prompt_video,
        "extract_audio_track",
        lambda _path: b"audio-bytes",
    )
    monkeypatch.setattr(
        reverse_prompt_video,
        "resolve",
        lambda *args, **kwargs: provider,
    )
    monkeypatch.setattr(reverse_prompt_video, "logger", captured)

    outcome = reverse_prompt_video.run_reverse_prompt_video_job(
        job_id,
        session_factory=auth_db,
    )
    expected_billing = reverse_prompt_video._aggregate_provider_usage(
        {},
        [
            _usage(
                model="gpt-4o-mini-transcribe",
                prompt_tokens=120,
                completion_tokens=20,
                cost_cents=1,
                credits="0.01",
                input_audio_tokens=120,
                output_text_tokens=20,
            ),
            _segment_result(
                {
                    "segment_index": 1,
                    "segment_start_sec": 0.0,
                    "segment_end_sec": 60.0,
                }
            ),
            _segment_result(
                {
                    "segment_index": 2,
                    "segment_start_sec": 60.0,
                    "segment_end_sec": 75.0,
                }
            ),
            _summary_result(
                {
                    "segment_analyses": [
                        _segment_result(
                            {
                                "segment_index": 1,
                                "segment_start_sec": 0.0,
                                "segment_end_sec": 60.0,
                            }
                        )["segment_analysis"],
                        _segment_result(
                            {
                                "segment_index": 2,
                                "segment_start_sec": 60.0,
                                "segment_end_sec": 75.0,
                            }
                        )["segment_analysis"],
                    ],
                    "audio_transcript": transcript,
                }
            ),
        ],
    )

    assert outcome == {"job_id": job_id, "status": "succeeded"}
    upstream = [
        fields
        for level, event, fields in captured.records
        if level == "info" and event == "reverse_prompt_video_upstream_call"
    ]
    assert [record["stage"] for record in upstream] == [
        "asr",
        "segment_1",
        "segment_2",
        "summary",
    ]
    assert all(_UPSTREAM_REQUIRED_FIELDS <= record.keys() for record in upstream)
    assert [record["input_video_tokens"] for record in upstream] == [
        0,
        2_000,
        1_000,
        0,
    ]
    assert [record["cached_tokens"] for record in upstream] == [0, 500, 200, 0]

    plan = next(
        fields
        for _, event, fields in captured.records
        if event == "reverse_prompt_video_segment_plan"
    )
    assert plan["duration_sec"] == 75.0
    assert plan["segment_count"] == 2
    assert plan["summary_required"] is True
    assert plan["segments"] == [
        {"index": 1, "start_sec": 0.0, "end_sec": 60.0},
        {"index": 2, "start_sec": 60.0, "end_sec": 75.0},
    ]

    proxies = [
        fields
        for level, event, fields in captured.records
        if level == "info" and event == "reverse_prompt_video_proxy_transcode"
    ]
    assert len(proxies) == 2
    assert all(record["executed"] is True for record in proxies)
    assert all(record["input_width"] == 720 for record in proxies)
    assert all(record["input_height"] == 1280 for record in proxies)
    assert all(record["input_size_bytes"] == len(b"source-video-bytes") for record in proxies)
    assert all(record["output_width"] == 360 for record in proxies)
    assert all(record["output_height"] == 640 for record in proxies)
    assert all(record["output_size_bytes"] == len(b"proxy-video") for record in proxies)
    assert all(record["resolution_source"] == "probed" for record in proxies)
    assert all(record["probe_error_type"] is None for record in proxies)
    assert all(record["probe_elapsed_ms"] >= 0 for record in proxies)
    assert all(record["elapsed_ms"] >= 0 for record in proxies)

    completed_segments = [
        fields
        for level, event, fields in captured.records
        if level == "info" and event == "reverse_prompt_video_segment_completed"
    ]
    assert [
        (record["segment_index"], record["start_sec"], record["end_sec"])
        for record in completed_segments
    ] == [(1, 0.0, 60.0), (2, 60.0, 75.0)]
    assert all(record["elapsed_ms"] >= 0 for record in completed_segments)

    asr_result = next(
        fields
        for _, event, fields in captured.records
        if event == "reverse_prompt_video_asr_result"
    )
    assert asr_result["transcript_chars"] == len(transcript)
    assert asr_result["has_text"] is True

    serialized_logs = json.dumps(captured.records, ensure_ascii=False, default=str)
    assert transcript not in serialized_logs
    assert "source-video-bytes" not in serialized_logs
    assert "audio-bytes" not in serialized_logs

    with auth_db() as result_db:
        stored_job = result_db.get(ReversePromptJob, job_id)
        usage = result_db.scalar(
            select(UsageRecord).where(UsageRecord.reverse_prompt_job_id == job_id)
        )
        subscription = result_db.scalar(
            select(Subscription).where(
                Subscription.tenant_id == auth_context["tenant_id"]
            )
        )
        assert stored_job.status == "succeeded"
        assert stored_job.cost_cents == expected_billing["cost_cents"]
        assert usage.status == "settled"
        assert usage.cost_cents == expected_billing["cost_cents"]
        assert usage.quantity == Decimal(str(expected_billing["total_tokens"]))
        assert subscription.quota_credits_reserved == 0


def test_native_usage_modality_details_reach_upstream_log(
    monkeypatch,
) -> None:
    from app.providers.reverse_prompt.apimart_gemini import (
        APIMartGeminiReversePromptProvider,
    )
    from app.services import reverse_prompt_video
    from app.services.reverse_prompt_usage import (
        begin_reverse_prompt_usage_capture,
        finish_reverse_prompt_usage_capture,
    )

    class _Response:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = ""

        def json(self) -> dict[str, Any]:
            segment = {
                "subject": "product",
                "scene": "studio",
                "composition": "portrait",
                "camera": "orbit",
                "lighting": "soft",
                "motion": "steady",
                "style": "commercial",
                "visible_text": [],
                "shot_list": [
                    {
                        "index": 0,
                        "start_sec": 0.0,
                        "end_sec": 10.0,
                        "visual": "product rotates",
                        "camera": "orbit",
                        "motion": "steady",
                        "transition": "cut",
                    }
                ],
                "segment_summary": "Product showcase.",
            }
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": json.dumps(segment)}],
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 685,
                    "promptTokensDetails": [
                        {"modality": "TEXT", "tokenCount": 25},
                        {"modality": "VIDEO", "tokenCount": 660},
                    ],
                    "cachedContentTokenCount": 60,
                    "candidatesTokenCount": 100,
                    "candidatesTokensDetails": [
                        {"modality": "TEXT", "tokenCount": 100},
                    ],
                    "thoughtsTokenCount": 40,
                    "totalTokenCount": 825,
                },
            }

    class _Session:
        def post(self, *args: Any, **kwargs: Any) -> _Response:
            return _Response()

    provider = APIMartGeminiReversePromptProvider(
        api_key="test-api-key",
        video_model="gemini-3.6-flash",
        session=_Session(),
    )
    captured = _CaptureLogger()
    monkeypatch.setattr(reverse_prompt_video, "logger", captured)
    context_token = reverse_prompt_video._observability_context.set(
        reverse_prompt_video._ObservabilityContext(
            job_id="modality-observability-job",
            tenant_id="modality-observability-tenant",
            duration_sec=75.0,
        )
    )
    usage_token, _ = begin_reverse_prompt_usage_capture()
    try:
        result = reverse_prompt_video._call_provider(
            lambda: provider.analyze_video_native_segment_sync(
                {
                    "video_bytes": b"video-bytes",
                    "duration_sec": 75.0,
                    "segment_index": 1,
                    "segment_start_sec": 0.0,
                    "segment_end_sec": 10.0,
                }
            ),
            stage="segment_1",
            operation_name="analyze_video_native_segment",
            segment_index=1,
            media_bytes=11,
            media_duration_sec=10.0,
            model_hint="gemini-3.6-flash",
        )
    finally:
        finish_reverse_prompt_usage_capture(usage_token)
        reverse_prompt_video._observability_context.reset(context_token)

    assert "input_video_tokens" not in result
    assert "input_text_tokens" not in result
    upstream = next(
        fields
        for level, event, fields in captured.records
        if level == "info" and event == "reverse_prompt_video_upstream_call"
    )
    assert upstream["prompt_tokens"] == 685
    assert upstream["completion_tokens"] == 140
    assert upstream["cached_tokens"] == 60
    assert upstream["input_modality_tokens_reported"] is True
    assert upstream["output_modality_tokens_reported"] is True
    assert upstream["input_text_tokens"] == 25
    assert upstream["input_video_tokens"] == 660
    assert upstream["input_audio_tokens"] == 0
    assert upstream["output_text_tokens"] == 100
    assert upstream["candidate_tokens"] == 100
    assert upstream["thought_tokens"] == 40
    assert upstream["video_tokens_per_second"] == 66.0
    assert upstream["input_credits_per_m"] == "12"
    assert upstream["output_credits_per_m"] == "60"
    assert upstream["cached_input_credits_per_m"] == "1.2"
    assert upstream["token_rate_source"] == "central_rate_table"
    serialized_logs = json.dumps(captured.records, default=str)
    assert "test-api-key" not in serialized_logs
    assert "dmlkZW8tYnl0ZXM=" not in serialized_logs


def test_openai_usage_details_reach_asr_log_without_changing_result(
    monkeypatch,
) -> None:
    from app.providers.reverse_prompt.apimart_gemini import (
        APIMartGeminiReversePromptProvider,
    )
    from app.services import reverse_prompt_video
    from app.services.reverse_prompt_usage import (
        begin_reverse_prompt_usage_capture,
        finish_reverse_prompt_usage_capture,
    )

    class _Response:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = ""

        def json(self) -> dict[str, Any]:
            return {
                "text": "short transcript",
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 20,
                    "total_tokens": 140,
                    "prompt_tokens_details": {
                        "cached_tokens": 30,
                        "text_tokens": 30,
                        "audio_tokens": 90,
                    },
                    "completion_tokens_details": {
                        "text_tokens": 18,
                        "reasoning_tokens": 2,
                    },
                },
                "credits": "0.01",
            }

    class _Session:
        def post(self, *args: Any, **kwargs: Any) -> _Response:
            return _Response()

    provider = APIMartGeminiReversePromptProvider(
        api_key="test-api-key",
        session=_Session(),
    )
    captured = _CaptureLogger()
    monkeypatch.setattr(reverse_prompt_video, "logger", captured)
    context_token = reverse_prompt_video._observability_context.set(
        reverse_prompt_video._ObservabilityContext(
            job_id="asr-details-job",
            tenant_id="asr-details-tenant",
            duration_sec=12.0,
        )
    )
    usage_token, _ = begin_reverse_prompt_usage_capture()
    try:
        result = reverse_prompt_video._call_provider(
            lambda: provider.transcribe_audio_sync(
                {
                    "audio_bytes": b"audio-bytes",
                    "filename": "source.mp3",
                    "language": "zh",
                }
            ),
            stage="asr",
            operation_name="transcribe_audio",
            media_bytes=11,
            frame_count=0,
            media_duration_sec=12.0,
            model_hint="gpt-4o-mini-transcribe",
        )
    finally:
        finish_reverse_prompt_usage_capture(usage_token)
        reverse_prompt_video._observability_context.reset(context_token)

    assert result["audio_transcript"] == "short transcript"
    assert "input_audio_tokens" not in result
    assert "input_text_tokens" not in result
    upstream = next(
        fields
        for level, event, fields in captured.records
        if level == "info" and event == "reverse_prompt_video_upstream_call"
    )
    assert upstream["cached_tokens"] == 30
    assert upstream["cache_tokens_reported"] is True
    assert upstream["input_modality_tokens_reported"] is True
    assert upstream["output_modality_tokens_reported"] is True
    assert upstream["input_text_tokens"] == 30
    assert upstream["input_audio_tokens"] == 90
    assert upstream["output_text_tokens"] == 18
    assert upstream["thought_tokens"] == 2
    assert upstream["input_credits_per_m"] == "10"
    assert upstream["output_credits_per_m"] == "40"
    assert upstream["cached_input_credits_per_m"] is None
    assert upstream["token_rate_source"] == "central_rate_table"


def test_unconfigured_model_log_never_inherits_another_models_rate(
    monkeypatch,
) -> None:
    from app.services import reverse_prompt_video
    from app.services.reverse_prompt_usage import (
        begin_reverse_prompt_usage_capture,
        finish_reverse_prompt_usage_capture,
    )

    captured = _CaptureLogger()
    monkeypatch.setattr(reverse_prompt_video, "logger", captured)
    usage_token, _ = begin_reverse_prompt_usage_capture()
    try:
        result = reverse_prompt_video._call_provider(
            lambda: {
                "provider": "apimart",
                "model": "future-unpriced-model",
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "credits": Decimal("0.01"),
                "cost_cents": 1,
                "cost_source": "provider_credits",
            },
            stage="analysis",
            operation_name="analyze_video",
        )
    finally:
        finish_reverse_prompt_usage_capture(usage_token)

    assert result["model"] == "future-unpriced-model"
    upstream = next(
        fields
        for level, event, fields in captured.records
        if level == "info" and event == "reverse_prompt_video_upstream_call"
    )
    assert upstream["input_credits_per_m"] is None
    assert upstream["output_credits_per_m"] is None
    assert upstream["cached_input_credits_per_m"] is None
    assert upstream["token_rate_source"] == "unconfigured_model"
    assert any(
        level == "warning"
        and event == "reverse_prompt_token_rate_unconfigured"
        and fields["model"] == "future-unpriced-model"
        for level, event, fields in captured.records
    )


def test_structured_retry_logs_each_paid_upstream_call_and_warns(
    monkeypatch,
) -> None:
    from app.services import reverse_prompt_video
    from app.services.reverse_prompt_usage import (
        begin_reverse_prompt_usage_capture,
        capture_reverse_prompt_usage,
        finish_reverse_prompt_usage_capture,
    )

    captured = _CaptureLogger()
    monkeypatch.setattr(reverse_prompt_video, "logger", captured)
    context_token = reverse_prompt_video._observability_context.set(
        reverse_prompt_video._ObservabilityContext(
            job_id="observability-job",
            tenant_id="observability-tenant",
            duration_sec=30.0,
        )
    )
    usage_token, _ = begin_reverse_prompt_usage_capture()

    def provider_operation() -> dict[str, Any]:
        first = _usage(
            model="gemini-3.6-flash",
            prompt_tokens=2_000,
            completion_tokens=20,
            cost_cents=2,
            credits="0.03",
            input_video_tokens=1_500,
        )
        second = _usage(
            model="gemini-3.6-flash",
            prompt_tokens=2_100,
            completion_tokens=80,
            cost_cents=4,
            credits="0.05",
            input_video_tokens=1_500,
        )
        assert capture_reverse_prompt_usage(first) is True
        assert capture_reverse_prompt_usage(second) is True
        return {
            **second,
            "prompt_tokens": 4_100,
            "completion_tokens": 100,
            "total_tokens": 4_200,
            "cost_cents": 6,
            "credits": Decimal("0.08"),
        }

    try:
        result = reverse_prompt_video._call_provider(
            provider_operation,
            stage="segment_1",
            operation_name="analyze_video_native_segment",
            segment_index=1,
            media_bytes=2_048,
            media_duration_sec=30.0,
            model_hint="gemini-3.6-flash",
        )
    finally:
        finish_reverse_prompt_usage_capture(usage_token)
        reverse_prompt_video._observability_context.reset(context_token)

    assert result["cost_cents"] == 6
    upstream = [
        fields
        for level, event, fields in captured.records
        if level == "info" and event == "reverse_prompt_video_upstream_call"
    ]
    assert [record["stage"] for record in upstream] == [
        "segment_1",
        "structured_retry",
    ]
    assert [record["cost_cents"] for record in upstream] == [2, 4]
    assert upstream[1]["parent_stage"] == "segment_1"
    assert upstream[0]["video_tokens_per_second"] == 50.0
    assert any(
        level == "warning"
        and event == "reverse_prompt_video_structured_retry"
        and fields["retry_count"] == 1
        for level, event, fields in captured.records
    )


def test_paid_asr_without_text_logs_usage_and_degradation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.providers.reverse_prompt.apimart_gemini import (
        APIMartGeminiReversePromptError,
    )
    from app.services import reverse_prompt_video
    from app.services.reverse_prompt_usage import (
        begin_reverse_prompt_usage_capture,
        finish_reverse_prompt_usage_capture,
    )

    captured = _CaptureLogger()
    monkeypatch.setattr(reverse_prompt_video, "logger", captured)
    monkeypatch.setattr(
        reverse_prompt_video,
        "extract_audio_track",
        lambda _path: b"paid-audio",
    )
    paid_usage = _usage(
        model="gpt-4o-mini-transcribe",
        prompt_tokens=1_795,
        completion_tokens=2,
        cost_cents=2,
        credits="0.02",
        input_audio_tokens=1_795,
        output_text_tokens=2,
    )

    def transcribe_audio(_payload):
        raise APIMartGeminiReversePromptError(
            "transcription returned no text",
            usage_results=[paid_usage],
        )

    context_token = reverse_prompt_video._observability_context.set(
        reverse_prompt_video._ObservabilityContext(
            job_id="asr-job",
            tenant_id="asr-tenant",
            duration_sec=179.0,
        )
    )
    usage_token, captured_usage = begin_reverse_prompt_usage_capture()
    try:
        transcript, result = reverse_prompt_video._best_effort_audio_transcript(
            SimpleNamespace(transcribe_audio=transcribe_audio),
            tmp_path / "source.mp4",
        )
    finally:
        finish_reverse_prompt_usage_capture(usage_token)
        reverse_prompt_video._observability_context.reset(context_token)

    assert transcript is None
    assert result is None
    assert [item["total_tokens"] for item in captured_usage] == [1_797]
    upstream = next(
        fields
        for level, event, fields in captured.records
        if level == "error" and event == "reverse_prompt_video_upstream_call"
    )
    assert upstream["stage"] == "asr"
    assert upstream["outcome"] == "failed"
    assert upstream["cost_cents"] == 2
    degraded = next(
        fields
        for level, event, fields in captured.records
        if level == "warning"
        and event == "reverse_prompt_audio_transcription_degraded"
    )
    assert degraded["paid_without_text"] is True
    assert degraded["prompt_tokens"] == 1_795
    assert degraded["completion_tokens"] == 2
    assert degraded["transcript_chars"] == 0


def test_proxy_failure_and_frames_skip_are_explicit_warnings(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.services import reverse_prompt_video

    captured = _CaptureLogger()
    monkeypatch.setattr(reverse_prompt_video, "logger", captured)
    monkeypatch.setattr(
        reverse_prompt_video,
        "build_video_analysis_proxy",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            reverse_prompt_video.ReversePromptVideoProcessingError(
                "injected proxy failure"
            )
        ),
    )
    context_token = reverse_prompt_video._observability_context.set(
        reverse_prompt_video._ObservabilityContext(
            job_id="proxy-job",
            tenant_id="proxy-tenant",
            input_width=1920,
            input_height=1080,
            input_size_bytes=10_000,
            duration_sec=30.0,
        )
    )
    try:
        with pytest.raises(
            reverse_prompt_video.ReversePromptVideoProcessingError,
            match="injected proxy failure",
        ):
            reverse_prompt_video._build_observed_video_analysis_proxy(
                tmp_path / "source.mp4",
                segment_index=1,
            )
        reverse_prompt_video._log_proxy_skipped(
            reason="analysis_mode_frames",
            segment_index=1,
        )
    finally:
        reverse_prompt_video._observability_context.reset(context_token)

    proxy_warnings = [
        fields
        for level, event, fields in captured.records
        if level == "warning"
        and event == "reverse_prompt_video_proxy_transcode"
    ]
    assert [(record["executed"], record["outcome"]) for record in proxy_warnings] == [
        (True, "failed"),
        (False, "skipped"),
    ]
    assert proxy_warnings[0]["reason"] == "ReversePromptVideoProcessingError"
    assert proxy_warnings[1]["reason"] == "analysis_mode_frames"
    assert proxy_warnings[0]["resolution_source"] == "transcode_failed"
    assert proxy_warnings[1]["resolution_source"] == "skipped"
    assert all(record["output_width"] is None for record in proxy_warnings)
    assert all(record["output_height"] is None for record in proxy_warnings)


def test_proxy_log_uses_probed_dimensions_instead_of_scale_contract(
    monkeypatch,
    tmp_path: Path,
    oversized_proxy_bytes: bytes,
) -> None:
    from app.services import reverse_prompt_video

    captured = _CaptureLogger()
    monkeypatch.setattr(reverse_prompt_video, "logger", captured)
    monkeypatch.setattr(
        reverse_prompt_video,
        "build_video_analysis_proxy",
        lambda *args, **kwargs: oversized_proxy_bytes,
    )
    context_token = reverse_prompt_video._observability_context.set(
        reverse_prompt_video._ObservabilityContext(
            job_id="proxy-probe-job",
            tenant_id="proxy-probe-tenant",
            input_width=720,
            input_height=1280,
            input_size_bytes=10_000,
            duration_sec=30.0,
        )
    )
    try:
        proxy_bytes = reverse_prompt_video._build_observed_video_analysis_proxy(
            tmp_path / "source.mp4",
            segment_index=1,
        )
    finally:
        reverse_prompt_video._observability_context.reset(context_token)

    assert proxy_bytes == oversized_proxy_bytes
    proxy_log = next(
        fields
        for level, event, fields in captured.records
        if level == "info" and event == "reverse_prompt_video_proxy_transcode"
    )
    assert (proxy_log["output_width"], proxy_log["output_height"]) == (1280, 720)
    assert proxy_log["resolution_source"] == "probed"
    assert proxy_log["output_size_bytes"] == len(oversized_proxy_bytes)
    assert proxy_log["probe_elapsed_ms"] >= 0


def test_proxy_probe_failure_logs_unknown_dimensions_without_changing_proxy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.services import reverse_prompt_video

    invalid_proxy_bytes = b"not-a-video"
    captured = _CaptureLogger()
    monkeypatch.setattr(reverse_prompt_video, "logger", captured)
    monkeypatch.setattr(
        reverse_prompt_video,
        "build_video_analysis_proxy",
        lambda *args, **kwargs: invalid_proxy_bytes,
    )
    context_token = reverse_prompt_video._observability_context.set(
        reverse_prompt_video._ObservabilityContext(
            job_id="proxy-probe-failure-job",
            tenant_id="proxy-probe-failure-tenant",
            input_width=720,
            input_height=1280,
            input_size_bytes=10_000,
            duration_sec=30.0,
        )
    )
    try:
        proxy_bytes = reverse_prompt_video._build_observed_video_analysis_proxy(
            tmp_path / "source.mp4",
            segment_index=1,
        )
    finally:
        reverse_prompt_video._observability_context.reset(context_token)

    assert proxy_bytes == invalid_proxy_bytes
    proxy_log = next(
        fields
        for level, event, fields in captured.records
        if level == "warning" and event == "reverse_prompt_video_proxy_transcode"
    )
    assert proxy_log["outcome"] == "succeeded"
    assert proxy_log["output_width"] is None
    assert proxy_log["output_height"] is None
    assert proxy_log["resolution_source"] == "probe_failed"
    assert proxy_log["probe_error_type"] == "ReversePromptVideoProcessingError"
    assert proxy_log["probe_elapsed_ms"] >= 0
