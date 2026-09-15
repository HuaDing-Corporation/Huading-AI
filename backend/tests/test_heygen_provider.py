import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest


def _body(model):
    if model == "avatar_iv":
        return {
            "type": "image",
            "image": {"type": "url", "url": "https://media.example/photo.jpg"},
            "audio_url": "https://media.example/audio.mp3",
            "engine": {"type": "avatar_iv"},
            "resolution": "1080p",
            "aspect_ratio": "9:16",
            "fit": "contain",
        }
    return {
        "video": {"type": "url", "url": "https://media.example/video.mp4"},
        "audio": {"type": "url", "url": "https://media.example/audio.mp3"},
        "mode": "precision",
        "keep_the_same_format": True,
        "enable_dynamic_duration": False,
        "enable_speech_enhancement": False,
    }


def _payload(model="avatar_iv", **overrides):
    body = _body(model)
    now = datetime.now(UTC)
    payload = {
        "request_body": body,
        "request_fingerprint": hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "idempotency_key": "test-heygen-one-identity",
        "submitted_at": now.isoformat(),
        "input_expires_at": (now + timedelta(hours=25)).isoformat(),
        "provider_job_id": None,
        "on_submitted": lambda job_id: None,
        "before_request": lambda: None,
    }
    return {**payload, **overrides}


@pytest.fixture
def provider_factory(monkeypatch):
    from app.providers.avatar.heygen import HeyGenAvatarProvider

    monkeypatch.setattr("app.providers.url_guard._ensure_public_host", lambda *args: None)

    def build(handler, **kwargs):
        return HeyGenAvatarProvider(
            api_key="fixture-only",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            input_hosts={"media.example"},
            result_hosts={"files.heygen.ai"},
            poll_interval_seconds=0,
            **kwargs,
        )

    return build


@pytest.mark.parametrize(
    "model,endpoint,id_field",
    [
        ("avatar_iv", "videos", "video_id"),
        ("lipsync_precision", "lipsyncs", "lipsync_id"),
    ],
)
@pytest.mark.asyncio
async def test_dual_contract_saves_identity_before_poll(
    provider_factory, model, endpoint, id_field
):
    seen = []
    saved = []

    def handler(request):
        seen.append(request)
        assert request.url.host == "api.heygen.com"
        if request.method == "POST":
            assert request.url.path == f"/v3/{endpoint}"
            assert json.loads(request.content) == _body(model)
            assert request.headers["Idempotency-Key"] == "test-heygen-one-identity"
            return httpx.Response(200, json={"data": {id_field: "job1"}})
        assert saved == ["job1"]
        assert request.url.path == f"/v3/{endpoint}/job1"
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "job1",
                    "status": "completed",
                    "duration": 8,
                    "video_url": "https://files.heygen.ai/video/test.mp4",
                }
            },
        )

    provider = provider_factory(handler)
    operation = provider.generate_avatar if model == "avatar_iv" else provider.generate_change_lips
    result = await operation(_payload(model, on_submitted=saved.append))
    assert result["model"] == model
    assert result["provider"] == "heygen"
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_resume_existing_job_never_posts_even_after_24h(provider_factory):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "job1",
                    "status": "completed",
                    "duration": 8,
                    "video_url": "https://files.heygen.ai/video/test.mp4",
                }
            },
        )

    provider = provider_factory(handler)
    result = await provider.generate_avatar(
        _payload(
            provider_job_id="job1",
            submitted_at=(datetime.now(UTC) - timedelta(days=2)).isoformat(),
        )
    )
    assert result["provider_job_id"] == "job1"
    assert calls == ["GET"]


@pytest.mark.parametrize("status", [409, 429, 500, 503])
@pytest.mark.asyncio
async def test_uncertain_submit_does_not_retry_or_report_terminal(provider_factory, status):
    from app.providers.avatar.heygen import HeyGenPending

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"code": "request_in_progress"}})

    provider = provider_factory(handler)
    with pytest.raises(HeyGenPending):
        await provider.generate_avatar(_payload())
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_post_timeout_same_identity_can_be_replayed(provider_factory):
    from app.providers.avatar.heygen import HeyGenPending

    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append((request.headers["Idempotency-Key"], request.content))
            if len(posts) == 1:
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            return httpx.Response(200, json={"data": {"video_id": "job1"}})
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "job1",
                    "status": "completed",
                    "duration": 8,
                    "video_url": "https://files.heygen.ai/video/test.mp4",
                }
            },
        )

    provider = provider_factory(handler)
    payload = _payload()
    with pytest.raises(HeyGenPending):
        await provider.generate_avatar(payload)
    await provider.generate_avatar(payload)
    assert posts[0] == posts[1]


@pytest.mark.parametrize(
    "overrides",
    [
        {"submitted_at": (datetime.now(UTC) - timedelta(hours=24)).isoformat()},
        {"input_expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
        {"request_fingerprint": "changed"},
    ],
)
@pytest.mark.asyncio
async def test_invalid_replay_identity_stops_before_network(provider_factory, overrides):
    from app.providers.avatar.heygen import HeyGenReviewRequired

    calls = []
    provider = provider_factory(lambda request: calls.append(request))
    with pytest.raises(HeyGenReviewRequired):
        await provider.generate_avatar(_payload(**overrides))
    assert calls == []


@pytest.mark.asyncio
async def test_poll_timeout_keeps_submitted_job(provider_factory):
    from app.providers.avatar.heygen import HeyGenPending

    saved = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"video_id": "job1"}})
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    provider = provider_factory(handler)
    with pytest.raises(HeyGenPending):
        await provider.generate_avatar(_payload(on_submitted=saved.append))
    assert saved == ["job1"]


@pytest.mark.asyncio
async def test_auth_redirect_is_not_followed(provider_factory):
    from app.providers.avatar.heygen import HeyGenReviewRequired

    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(307, headers={"Location": "https://attacker.example/capture"})

    provider = provider_factory(handler)
    with pytest.raises(HeyGenReviewRequired):
        await provider.generate_avatar(_payload())
    assert calls == ["api.heygen.com"]


@pytest.mark.parametrize(
    "url",
    [
        "http://media.example/i.jpg",
        "https://media.example.attacker.test/i.jpg",
        "https://user:password@media.example/i.jpg",
        "https://media.example:8443/i.jpg",
        "https://127.0.0.1/i.jpg",
    ],
)
def test_image_request_rejects_unsafe_input(provider_factory, url):
    from app.providers.avatar.heygen import HeyGenError

    provider = provider_factory(lambda request: None)
    with pytest.raises(HeyGenError):
        provider.build_request(
            {"image_url": url, "audio_url": "https://media.example/a.mp3"}, model="avatar_iv"
        )


def test_no_key_has_no_fallback():
    from app.providers.avatar.heygen import HeyGenAvatarProvider, HeyGenError

    with pytest.raises(HeyGenError, match="configured"):
        HeyGenAvatarProvider(api_key="")


@pytest.mark.parametrize("host", ["files.heygen.ai", "files2.heygen.ai"])
@pytest.mark.asyncio
async def test_default_result_hosts_download_without_api_credentials(monkeypatch, host):
    from app.providers.avatar.heygen import HeyGenAvatarProvider

    monkeypatch.setattr("app.providers.url_guard._ensure_public_host", lambda *args: None)
    calls = []

    def handler(request):
        calls.append(request.url.host)
        assert "x-api-key" not in request.headers
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"fixture-video")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HeyGenAvatarProvider(api_key="fixture-only", http_client=client)
        assert await provider.download_video(f"https://{host}/video/test.mp4") == b"fixture-video"
    assert calls == [host]


@pytest.mark.parametrize(
    "host", ["files2.heygen.ai.attacker.test", "fakefiles2.heygen.ai", "arbitrary.amazonaws.com"]
)
@pytest.mark.asyncio
async def test_default_result_hosts_reject_lookalikes_before_network(monkeypatch, host):
    from app.providers.avatar.heygen import HeyGenAvatarProvider, HeyGenError

    monkeypatch.setattr("app.providers.url_guard._ensure_public_host", lambda *args: None)
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: calls.append(request))
    ) as client:
        provider = HeyGenAvatarProvider(api_key="fixture-only", http_client=client)
        with pytest.raises(HeyGenError, match="not allowed"):
            await provider.download_video(f"https://{host}/video/test.mp4")
    assert calls == []
