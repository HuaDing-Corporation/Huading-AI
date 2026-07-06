import base64

import pytest


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHTTP:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = list(responses)
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
        return self.responses.pop(0)

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
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_doubao_voice_clone_uploads_base64_audio_with_megatts_contract():
    from app.providers.voice_clone.doubao import DoubaoVoiceCloneProvider

    http = _FakeHTTP(
        [
            _FakeResponse({"message": "ok"}),
            _FakeResponse({"status": 2}),
        ]
    )
    provider = DoubaoVoiceCloneProvider(
        appid="doubao-appid",
        access_token="seed-token",
        resource_id="volc.megatts.voiceclone",
        endpoint="https://openspeech.bytedance.com/api/v1/mega_tts/audio/upload",
        status_endpoint="https://openspeech.bytedance.com/api/v1/mega_tts/status",
        http_client=http,
        request_timeout_seconds=15,
        poll_interval_seconds=0,
        timeout_seconds=1,
        model_type=4,
    )

    result = await provider.clone_voice(
        {
            "tenant_id": "tenant-brand",
            "brand_voice_id": "brand-voice-unit",
            "name": "Store Voice",
            "speaker_id": "S_brand_001",
            "source_audio_asset_id": "asset-voice-unit",
            "source_audio_storage_key": "tenants/tenant-brand/uploads/voice.wav",
            "source_audio_bytes": b"WAVDATA",
            "source_audio_mime_type": "audio/wav",
        }
    )

    assert result == {
        "speaker_id": "S_brand_001",
        "status": "ready",
        "provider": "doubao-voice-clone",
    }
    call = http.calls[0]
    assert call["url"] == "https://openspeech.bytedance.com/api/v1/mega_tts/audio/upload"
    assert call["headers"]["Authorization"] == "Bearer;seed-token"
    assert call["headers"]["Resource-Id"] == "volc.megatts.voiceclone"
    assert "X-Api-App-Id" not in call["headers"]
    assert "X-Api-Resource-Id" not in call["headers"]
    assert call["timeout"] == 15
    assert call["json"] == {
        "appid": "doubao-appid",
        "speaker_id": "S_brand_001",
        "audios": [
            {
                "audio_bytes": base64.b64encode(b"WAVDATA").decode("ascii"),
                "audio_format": "wav",
            }
        ],
        "source": 2,
        "language": 0,
        "model_type": 4,
    }
    status_call = http.calls[1]
    assert status_call["url"] == "https://openspeech.bytedance.com/api/v1/mega_tts/status"
    assert status_call["json"] == {"appid": "doubao-appid", "speaker_id": "S_brand_001"}


@pytest.mark.asyncio
async def test_doubao_voice_clone_rejects_http_200_upload_base_resp_error():
    from app.providers.voice_clone.doubao import DoubaoVoiceCloneError, DoubaoVoiceCloneProvider

    http = _FakeHTTP(
        [
            _FakeResponse(
                {
                    "BaseResp": {
                        "StatusCode": 3001,
                        "StatusMessage": "speaker slot has no remaining quota",
                    }
                }
            ),
            _FakeResponse({"status": 2}),
        ]
    )
    provider = DoubaoVoiceCloneProvider(
        appid="doubao-appid",
        access_token="seed-token",
        http_client=http,
        poll_interval_seconds=0,
        timeout_seconds=1,
    )

    with pytest.raises(DoubaoVoiceCloneError, match="speaker slot has no remaining quota"):
        await provider.clone_voice(
            {
                "speaker_id": "S_brand_001",
                "source_audio_bytes": b"MP3",
                "source_audio_mime_type": "audio/mpeg",
            }
        )

    assert len(http.calls) == 1


@pytest.mark.asyncio
async def test_doubao_voice_clone_transcodes_webm_recording_to_wav_upload(monkeypatch):
    from app.providers.voice_clone import doubao as module
    from app.providers.voice_clone.doubao import DoubaoVoiceCloneProvider

    transcode_calls: list[dict] = []

    def fake_transcode(audio_bytes: bytes, *, suffix: str) -> bytes:
        transcode_calls.append({"audio_bytes": audio_bytes, "suffix": suffix})
        return b"WAV"

    monkeypatch.setattr(module, "_transcode_audio_to_wav", fake_transcode, raising=False)
    http = _FakeHTTP(
        [
            _FakeResponse({"message": "ok"}),
            _FakeResponse({"status": 2}),
        ]
    )
    provider = DoubaoVoiceCloneProvider(
        appid="doubao-appid",
        access_token="seed-token",
        http_client=http,
        poll_interval_seconds=0,
        timeout_seconds=1,
    )

    result = await provider.clone_voice(
        {
            "speaker_id": "S_brand_001",
            "source_audio_storage_key": "tenants/t/uploads/browser.webm",
            "source_audio_bytes": b"WEBM",
            "source_audio_mime_type": "audio/webm;codecs=opus",
        }
    )

    assert result["status"] == "ready"
    assert transcode_calls == [{"audio_bytes": b"WEBM", "suffix": ".webm"}]
    assert http.calls[0]["json"]["audios"] == [
        {
            "audio_bytes": base64.b64encode(b"WAV").decode("ascii"),
            "audio_format": "wav",
        }
    ]


@pytest.mark.asyncio
async def test_doubao_voice_clone_maps_training_then_active_status_to_ready():
    from app.providers.voice_clone.doubao import DoubaoVoiceCloneProvider

    http = _FakeHTTP(
        [
            _FakeResponse({"message": "ok"}),
            _FakeResponse({"status": 1}),
            _FakeResponse({"data": {"status": 4}}),
        ]
    )
    provider = DoubaoVoiceCloneProvider(
        appid="doubao-appid",
        access_token="seed-token",
        http_client=http,
        poll_interval_seconds=0,
        timeout_seconds=1,
    )

    result = await provider.clone_voice(
        {
            "speaker_id": "S_brand_001",
            "source_audio_bytes": b"MP3",
            "source_audio_mime_type": "audio/mpeg",
        }
    )

    assert result["status"] == "ready"
    assert len(http.calls) == 3


@pytest.mark.asyncio
async def test_doubao_voice_clone_maps_not_found_status_zero_to_provider_error():
    from app.providers.voice_clone.doubao import DoubaoVoiceCloneError, DoubaoVoiceCloneProvider

    http = _FakeHTTP(
        [
            _FakeResponse({"message": "ok"}),
            _FakeResponse({"status": 0, "message": "speaker not found"}),
        ]
    )
    provider = DoubaoVoiceCloneProvider(
        appid="doubao-appid",
        access_token="seed-token",
        http_client=http,
        poll_interval_seconds=0,
        timeout_seconds=1,
    )

    with pytest.raises(DoubaoVoiceCloneError, match="speaker not found"):
        await provider.clone_voice(
            {
                "speaker_id": "S_brand_001",
                "source_audio_bytes": b"MP3",
                "source_audio_mime_type": "audio/mpeg",
            }
        )


@pytest.mark.asyncio
async def test_doubao_voice_clone_maps_failed_status_to_provider_error():
    from app.providers.voice_clone.doubao import DoubaoVoiceCloneError, DoubaoVoiceCloneProvider

    http = _FakeHTTP(
        [
            _FakeResponse({"message": "ok"}),
            _FakeResponse({"status": 3, "message": "training failed"}),
        ]
    )
    provider = DoubaoVoiceCloneProvider(
        appid="doubao-appid",
        access_token="seed-token",
        http_client=http,
        poll_interval_seconds=0,
        timeout_seconds=1,
    )

    with pytest.raises(DoubaoVoiceCloneError, match="training failed"):
        await provider.clone_voice(
            {
                "speaker_id": "S_brand_001",
                "source_audio_bytes": b"MP3",
                "source_audio_mime_type": "audio/mpeg",
            }
        )


@pytest.mark.asyncio
async def test_doubao_voice_clone_delete_is_local_slot_noop():
    from app.providers.voice_clone.doubao import DoubaoVoiceCloneProvider

    http = _FakeHTTP([])
    provider = DoubaoVoiceCloneProvider(
        appid="doubao-appid",
        access_token="seed-token",
        http_client=http,
    )

    result = await provider.delete_voice({"speaker_id": "S_brand_001"})

    assert result == {"released": True, "remote": False}
    assert http.calls == []
