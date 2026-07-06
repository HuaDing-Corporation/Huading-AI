import hashlib
import hmac
import json
from datetime import UTC, datetime
from urllib.parse import quote

import pytest

from app.providers.avatar import omnihuman
from app.providers.avatar.omnihuman import OmniHumanProvider, OmniHumanProviderError


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class _Http:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(
        self,
        url: str,
        *,
        params: dict,
        json: dict | None = None,
        data: bytes | None = None,
        headers: dict,
        timeout: float,
    ):
        self.calls.append(
            {
                "url": url,
                "params": params,
                "json": json,
                "data": data,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return _Response(self.responses.pop(0))


def _hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _expected_volcengine_authorization(
    *,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    x_date: str,
    params: dict[str, str],
    body_bytes: bytes,
) -> str:
    short_date = x_date[:8]
    payload_hash = hashlib.sha256(body_bytes).hexdigest()
    canonical_query = "&".join(
        f"{quote(key, safe='')}={quote(value, safe='')}" for key, value in sorted(params.items())
    )
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_headers = (
        "content-type:application/json\n"
        "host:visual.volcengineapi.com\n"
        f"x-content-sha256:{payload_hash}\n"
        f"x-date:{x_date}\n"
    )
    canonical_request = "\n".join(
        [
            "POST",
            "/",
            canonical_query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join(
        [
            "HMAC-SHA256",
            x_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    k_date = _hmac_sha256(secret_key.encode("utf-8"), short_date)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    k_signing = _hmac_sha256(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return (
        f"HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


def test_omnihuman_uses_volcengine_hmac_sha256_authorization(monkeypatch) -> None:
    fixed_now = datetime(2026, 6, 19, 12, 34, 56, tzinfo=UTC)
    monkeypatch.setattr(omnihuman, "_utcnow", lambda: fixed_now, raising=False)
    provider = OmniHumanProvider(
        access_key="AKLTEXAMPLE",
        secret_key="secret-example",
        region="cn-north-1",
        http_client=_Http([]),
    )
    params = {"Action": "CVSubmitTask", "Version": "2022-08-31"}
    body_bytes = json.dumps(
        {"req_key": "jimeng_realman_avatar_picture_omni_v15", "seed": -1},
        separators=(",", ":"),
    ).encode("utf-8")

    headers = provider._signed_headers(params, body_bytes)

    assert headers["Content-Type"] == "application/json"
    assert headers["X-Date"] == "20260619T123456Z"
    assert headers["X-Content-Sha256"] == hashlib.sha256(body_bytes).hexdigest()
    assert headers["Authorization"] == _expected_volcengine_authorization(
        access_key="AKLTEXAMPLE",
        secret_key="secret-example",
        region="cn-north-1",
        service="cv",
        x_date="20260619T123456Z",
        params=params,
        body_bytes=body_bytes,
    )


def test_omnihuman_post_json_sends_exact_signed_body_bytes(monkeypatch) -> None:
    fixed_now = datetime(2026, 6, 19, 12, 34, 56, tzinfo=UTC)
    monkeypatch.setattr(omnihuman, "_utcnow", lambda: fixed_now, raising=False)
    http = _Http([{"code": 10000, "data": {"task_id": "cv-task-1"}}])
    provider = OmniHumanProvider(
        access_key="AKLTEXAMPLE",
        secret_key="secret-example",
        region="cn-north-1",
        http_client=http,
    )

    provider._post_json(
        "CVSubmitTask",
        {"req_key": "jimeng_realman_avatar_picture_omni_v15", "seed": -1},
    )

    call = http.calls[0]
    expected_body = b'{"req_key":"jimeng_realman_avatar_picture_omni_v15","seed":-1}'
    assert call["json"] is None
    assert call["data"] == expected_body
    assert call["headers"]["X-Content-Sha256"] == hashlib.sha256(expected_body).hexdigest()


def test_omnihuman_submit_poll_sends_locked_req_key_and_aigc_meta(monkeypatch) -> None:
    http = _Http(
        [
            {"code": 10000, "data": {"task_id": "cv-task-1"}},
            {"code": 10000, "data": {"status": "in_queue"}},
            {
                "code": 10000,
                "data": {
                    "status": "done",
                    "video_url": "https://visual.example/result.mp4",
                    "aigc_meta_tagged": True,
                },
            },
        ]
    )
    monkeypatch.setattr("app.providers.avatar.omnihuman.time.sleep", lambda *_: None)
    provider = OmniHumanProvider(
        access_key="ak",
        secret_key="sk",
        region="cn-north-1",
        http_client=http,
        request_timeout_seconds=7,
        poll_interval_seconds=0,
        timeout_seconds=30,
        allowed_hosts={"assets.example", "visual.example", "visual.volcengineapi.com"},
    )

    result = provider.generate_avatar_sync(
        {
            "image_url": "https://assets.example/avatar.png",
            "audio_url": "https://assets.example/audio.mp3",
            "prompt": "host speaks",
            "aigc_meta": {
                "content_producer": "Huading",
                "producer_id": "tenant-a",
                "content_propagator": "Huading",
                "propagate_id": "task-a",
            },
        }
    )

    assert result["task_id"] == "cv-task-1"
    assert result["video_url"] == "https://visual.example/result.mp4"
    submit, poll1, poll2 = http.calls
    submit_body = json.loads(submit["data"].decode("utf-8"))
    poll2_body = json.loads(poll2["data"].decode("utf-8"))
    assert submit["url"] == "https://visual.volcengineapi.com"
    assert submit["params"] == {"Action": "CVSubmitTask", "Version": "2022-08-31"}
    assert submit_body["req_key"] == "jimeng_realman_avatar_picture_omni_v15"
    assert submit_body["image_url"] == "https://assets.example/avatar.png"
    assert submit_body["audio_url"] == "https://assets.example/audio.mp3"
    assert "Authorization" in submit["headers"]
    assert submit["timeout"] == 7
    assert poll1["params"]["Action"] == "CVGetResult"
    assert "aigc_meta" in poll2_body["req_json"]


def test_omnihuman_pending_polls_emit_progress_heartbeat(monkeypatch) -> None:
    http = _Http(
        [
            {"code": 10000, "data": {"task_id": "cv-job-1"}},
            {"code": 10000, "data": {"status": "in_queue"}},
            {"code": 10000, "data": {"status": "processing"}},
            {
                "code": 10000,
                "data": {
                    "status": "done",
                    "video_url": "https://visual.example/result.mp4",
                    "aigc_meta_tagged": True,
                },
            },
        ]
    )
    heartbeats: list[dict] = []
    monkeypatch.setattr("app.providers.avatar.omnihuman.time.sleep", lambda *_: None)
    provider = OmniHumanProvider(
        access_key="ak",
        secret_key="sk",
        region="cn-north-1",
        http_client=http,
        poll_interval_seconds=0,
        allowed_hosts={"assets.example", "visual.example", "visual.volcengineapi.com"},
    )

    provider.generate_avatar_sync(
        {
            "image_url": "https://assets.example/avatar.png",
            "audio_url": "https://assets.example/audio.mp3",
            "aigc_meta": {},
            "progress_callback": heartbeats.append,
        }
    )

    assert [item["status"] for item in heartbeats] == ["in_queue", "processing"]
    assert [item["poll_count"] for item in heartbeats] == [1, 2]


def test_omnihuman_audit_error_is_not_retried() -> None:
    http = _Http([{"code": 50411, "message": "image audit failed"}])
    provider = OmniHumanProvider(
        access_key="ak",
        secret_key="sk",
        region="cn-north-1",
        http_client=http,
        poll_interval_seconds=0,
        allowed_hosts={"assets.example", "visual.volcengineapi.com"},
    )

    with pytest.raises(OmniHumanProviderError, match="image audit failed"):
        provider.generate_avatar_sync(
            {
                "image_url": "https://assets.example/avatar.png",
                "audio_url": "https://assets.example/audio.mp3",
                "aigc_meta": {},
            }
        )

    assert len(http.calls) == 1


def test_omnihuman_rejects_non_whitelisted_media_urls_before_submit() -> None:
    http = _Http([{"code": 10000, "data": {"task_id": "should-not-submit"}}])
    provider = OmniHumanProvider(
        access_key="ak",
        secret_key="sk",
        region="cn-north-1",
        http_client=http,
        allowed_hosts={"assets.example", "visual.volcengineapi.com"},
    )

    with pytest.raises(OmniHumanProviderError, match="not allowed|whitelist"):
        provider.generate_avatar_sync(
            {
                "image_url": "https://evil.example/avatar.png",
                "audio_url": "https://assets.example/audio.mp3",
                "aigc_meta": {},
            }
        )

    assert http.calls == []


def test_omnihuman_allows_configured_result_host_suffix(monkeypatch) -> None:
    http = _Http(
        [
            {"code": 10000, "data": {"task_id": "cv-task-1"}},
            {
                "code": 10000,
                "data": {
                    "status": "done",
                    "video_url": "https://v26-aiop.aigc-cloud.com/result.mp4",
                    "aigc_meta_tagged": True,
                },
            },
        ]
    )
    monkeypatch.setattr("app.providers.avatar.omnihuman.time.sleep", lambda *_: None)
    provider = OmniHumanProvider(
        access_key="ak",
        secret_key="sk",
        region="cn-north-1",
        http_client=http,
        poll_interval_seconds=0,
        allowed_hosts={"assets.example", "visual.volcengineapi.com"},
        result_host_suffixes={"aigc-cloud.com"},
    )

    result = provider.generate_avatar_sync(
        {
            "image_url": "https://assets.example/avatar.png",
            "audio_url": "https://assets.example/audio.mp3",
            "aigc_meta": {},
        }
    )

    assert result["video_url"] == "https://v26-aiop.aigc-cloud.com/result.mp4"


def test_omnihuman_keeps_media_urls_strict_when_result_suffix_configured() -> None:
    http = _Http([{"code": 10000, "data": {"task_id": "should-not-submit"}}])
    provider = OmniHumanProvider(
        access_key="ak",
        secret_key="sk",
        region="cn-north-1",
        http_client=http,
        allowed_hosts={"assets.example", "visual.volcengineapi.com"},
        result_host_suffixes={"aigc-cloud.com"},
    )

    with pytest.raises(OmniHumanProviderError, match="not allowed|whitelist"):
        provider.generate_avatar_sync(
            {
                "image_url": "https://v26-aiop.aigc-cloud.com/avatar.png",
                "audio_url": "https://assets.example/audio.mp3",
                "aigc_meta": {},
            }
        )

    assert http.calls == []


def test_omnihuman_change_lips_submit_poll_prefers_resp_data_url(monkeypatch) -> None:
    http = _Http(
        [
            {"code": 10000, "data": {"task_id": "change-lips-1"}},
            {"code": 10000, "data": {"status": "processing"}},
            {
                "code": 10000,
                "data": {
                    "status": "done",
                    "video_url": "",
                    "resp_data": json.dumps(
                        {"url": "https://visual.example/change-lips-basic.mp4"}
                    ),
                },
            },
        ]
    )
    heartbeats: list[dict] = []
    monkeypatch.setattr("app.providers.avatar.omnihuman.time.sleep", lambda *_: None)
    provider = OmniHumanProvider(
        access_key="ak",
        secret_key="sk",
        region="cn-north-1",
        http_client=http,
        poll_interval_seconds=0,
        timeout_seconds=30,
        allowed_hosts={"assets.example", "visual.example", "visual.volcengineapi.com"},
        change_lips_lite_req_key="realman_change_lips",
        change_lips_basic_req_key="realman_change_lips_basic_chimera",
    )

    result = provider.generate_change_lips_sync(
        {
            "video_url": "https://assets.example/avatar-source.mp4",
            "audio_url": "https://assets.example/voice.mp3",
            "tier": "basic",
            "align_audio": True,
            "align_audio_reverse": True,
            "templ_start_seconds": 1.5,
            "open_sr": True,
            "separate_vocal": False,
            "open_scenedet": True,
            "progress_callback": heartbeats.append,
        }
    )

    assert result["task_id"] == "change-lips-1"
    assert result["video_url"] == "https://visual.example/change-lips-basic.mp4"
    assert result["tier"] == "basic"
    submit, poll1, poll2 = http.calls
    submit_body = json.loads(submit["data"].decode("utf-8"))
    poll_body = json.loads(poll2["data"].decode("utf-8"))
    assert submit["params"] == {
        "Action": "RealmanChangeLipsSubmitTask",
        "Version": "2024-06-06",
    }
    assert submit_body["req_key"] == "realman_change_lips_basic_chimera"
    assert submit_body["url"] == "https://assets.example/avatar-source.mp4"
    assert submit_body["pure_audio_url"] == "https://assets.example/voice.mp3"
    assert submit_body["align_audio"] is True
    assert submit_body["align_audio_reverse"] is True
    assert submit_body["templ_start_seconds"] == 1.5
    assert submit_body["open_sr"] is True
    assert submit_body["separate_vocal"] is False
    assert submit_body["open_scenedet"] is True
    assert "/cn-beijing/cv/request" in submit["headers"]["Authorization"]
    assert poll1["params"]["Action"] == "RealmanChangeLipsGetResult"
    assert poll_body["req_key"] == "realman_change_lips_basic_chimera"
    assert poll_body["task_id"] == "change-lips-1"
    assert [item["status"] for item in heartbeats] == ["processing"]


def test_omnihuman_change_lips_falls_back_to_data_video_url(monkeypatch) -> None:
    http = _Http(
        [
            {"code": 10000, "data": {"task_id": "change-lips-2"}},
            {
                "code": 10000,
                "data": {
                    "status": "done",
                    "video_url": "https://visual.example/change-lips-lite.mp4",
                },
            },
        ]
    )
    monkeypatch.setattr("app.providers.avatar.omnihuman.time.sleep", lambda *_: None)
    provider = OmniHumanProvider(
        access_key="ak",
        secret_key="sk",
        region="cn-beijing",
        http_client=http,
        poll_interval_seconds=0,
        allowed_hosts={"assets.example", "visual.example", "visual.volcengineapi.com"},
    )

    result = provider.generate_change_lips_sync(
        {
            "video_url": "https://assets.example/avatar-source.mp4",
            "audio_url": "https://assets.example/voice.mp3",
            "tier": "lite",
            "align_audio_reverse": True,
            "open_sr": True,
        }
    )

    assert result["video_url"] == "https://visual.example/change-lips-lite.mp4"
    submit_body = json.loads(http.calls[0]["data"].decode("utf-8"))
    assert submit_body["align_audio_reverse"] is True
    assert "open_sr" not in submit_body


def test_omnihuman_change_lips_maps_known_video_errors_to_friendly_message() -> None:
    http = _Http([{"code": 50411, "message": "ECVideoDecodeError: bad stream"}])
    provider = OmniHumanProvider(
        access_key="ak",
        secret_key="sk",
        region="cn-beijing",
        http_client=http,
        allowed_hosts={"assets.example", "visual.volcengineapi.com"},
    )

    with pytest.raises(OmniHumanProviderError) as exc_info:
        provider.generate_change_lips_sync(
            {
                "video_url": "https://assets.example/avatar-source.mp4",
                "audio_url": "https://assets.example/voice.mp3",
                "tier": "lite",
            }
        )

    assert "ECVideoDecodeError" not in str(exc_info.value)
    assert "无法解码" in str(exc_info.value)
