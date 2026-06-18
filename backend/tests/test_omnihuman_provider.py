import pytest

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

    def post(self, url: str, *, params: dict, json: dict, headers: dict, timeout: float):
        self.calls.append(
            {
                "url": url,
                "params": params,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return _Response(self.responses.pop(0))


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
    assert submit["url"] == "https://visual.volcengineapi.com"
    assert submit["params"] == {"Action": "CVSubmitTask", "Version": "2022-08-31"}
    assert submit["json"]["req_key"] == "jimeng_realman_avatar_picture_omni_v15"
    assert submit["json"]["image_url"] == "https://assets.example/avatar.png"
    assert submit["json"]["audio_url"] == "https://assets.example/audio.mp3"
    assert "Authorization" in submit["headers"]
    assert submit["timeout"] == 7
    assert poll1["params"]["Action"] == "CVGetResult"
    assert "aigc_meta" in poll2["json"]["req_json"]


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
