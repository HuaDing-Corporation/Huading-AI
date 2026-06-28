import pytest


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHTTP:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post(self, url: str, *, json: dict, headers: dict, timeout: float):
        self.calls.append(
            {
                "method": "post",
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return self.response

    def delete(self, url: str, *, json: dict, headers: dict, timeout: float):
        self.calls.append(
            {
                "method": "delete",
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return self.response


@pytest.mark.asyncio
async def test_doubao_voice_clone_sends_seed_tts_auth_headers_with_audio_url():
    from app.providers.voice_clone.doubao import DoubaoVoiceCloneProvider

    http = _FakeHTTP(
        _FakeResponse({"speaker_id": "brand-speaker-001", "status": "ready"})
    )
    provider = DoubaoVoiceCloneProvider(
        appid="doubao-appid",
        access_token="seed-token",
        resource_id="seed-icl-2.0",
        endpoint="https://openspeech.bytedance.com/api/v3/voice-clone",
        http_client=http,
        request_timeout_seconds=15,
    )

    result = await provider.clone_voice(
        {
            "tenant_id": "tenant-brand",
            "brand_voice_id": "brand-voice-unit",
            "name": "Store Voice",
            "source_audio_asset_id": "asset-voice-unit",
            "source_audio_url": "https://storage.test/tenants/tenant-brand/uploads/voice.wav",
            "source_audio_mime_type": "audio/wav",
        }
    )

    assert result == {
        "speaker_id": "brand-speaker-001",
        "status": "ready",
        "provider": "doubao-voice-clone",
    }
    call = http.calls[0]
    assert call["url"] == "https://openspeech.bytedance.com/api/v3/voice-clone"
    assert call["headers"]["X-Api-App-Id"] == "doubao-appid"
    assert call["headers"]["X-Api-Access-Key"] == "seed-token"
    assert call["headers"]["X-Api-Resource-Id"] == "seed-icl-2.0"
    assert "Authorization" not in call["headers"]
    assert call["timeout"] == 15
    assert call["json"] == {
        "speaker_name": "Store Voice",
        "audio": {
            "url": "https://storage.test/tenants/tenant-brand/uploads/voice.wav",
            "mime_type": "audio/wav",
        },
        "metadata": {
            "tenant_id": "tenant-brand",
            "brand_voice_id": "brand-voice-unit",
            "source_audio_asset_id": "asset-voice-unit",
        },
    }
    assert "base64" not in str(call["json"]).lower()
    assert "storage_key" not in call["json"]["audio"]


@pytest.mark.asyncio
async def test_doubao_voice_clone_releases_speaker_best_effort_payload():
    from app.providers.voice_clone.doubao import DoubaoVoiceCloneProvider

    http = _FakeHTTP(_FakeResponse({"released": True}))
    provider = DoubaoVoiceCloneProvider(
        appid="doubao-appid",
        access_token="seed-token",
        endpoint="https://openspeech.bytedance.com/api/v3/voice-clone",
        http_client=http,
    )

    result = await provider.delete_voice(
        {
            "tenant_id": "tenant-brand",
            "brand_voice_id": "brand-voice-unit",
            "speaker_id": "brand-speaker-001",
        }
    )

    assert result == {"released": True}
    call = http.calls[0]
    assert call["method"] == "delete"
    assert call["json"] == {
        "speaker_id": "brand-speaker-001",
        "metadata": {
            "tenant_id": "tenant-brand",
            "brand_voice_id": "brand-voice-unit",
        },
    }
