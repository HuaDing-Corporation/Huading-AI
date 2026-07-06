import pytest


class _FakeEnrollment:
    def __init__(self) -> None:
        self.create_calls = []
        self.delete_calls = []

    def create_voice(self, target_model: str, prefix: str, url: str) -> str:
        self.create_calls.append(
            {"target_model": target_model, "prefix": prefix, "url": url}
        )
        return "cosy-voice-001"

    def delete_voice(self, voice_id: str) -> None:
        self.delete_calls.append(voice_id)


class _FakeSynthesizer:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.instances.append(self)

    def call(self, text: str, timeout_millis=None):
        self.calls.append({"text": text, "timeout_millis": timeout_millis})
        return b"MP3"


@pytest.mark.asyncio
async def test_cosyvoice_clone_uses_generated_safe_prefix_and_audio_url():
    from app.providers.voice_clone.cosyvoice import CosyVoiceCloneProvider

    enrollment = _FakeEnrollment()
    provider = CosyVoiceCloneProvider(
        api_key="dashscope-key",
        target_model="cosyvoice-v3.5-plus",
        enrollment_service=enrollment,
    )

    result = await provider.clone_voice(
        {
            "brand_voice_id": "中文-id-1234567890",
            "name": "中文店铺音色超长名字",
            "source_audio_url": "https://storage.test/audio.wav",
        }
    )

    assert result == {
        "speaker_id": "cosy-voice-001",
        "status": "ready",
        "provider": "cosyvoice-voice-clone",
    }
    assert enrollment.create_calls == [
        {
            "target_model": "cosyvoice-v3.5-plus",
            "prefix": "bvid123456",
            "url": "https://storage.test/audio.wav",
        }
    ]


@pytest.mark.asyncio
async def test_cosyvoice_delete_releases_remote_voice():
    from app.providers.voice_clone.cosyvoice import CosyVoiceCloneProvider

    enrollment = _FakeEnrollment()
    provider = CosyVoiceCloneProvider(
        api_key="dashscope-key",
        target_model="cosyvoice-v3.5-plus",
        enrollment_service=enrollment,
    )

    result = await provider.delete_voice({"speaker_id": "cosy-voice-001"})

    assert result == {"released": True}
    assert enrollment.delete_calls == ["cosy-voice-001"]


@pytest.mark.asyncio
async def test_cosyvoice_synthesize_writes_mp3(tmp_path):
    from app.providers.voice_clone.cosyvoice import CosyVoiceCloneProvider

    _FakeSynthesizer.instances = []
    provider = CosyVoiceCloneProvider(
        api_key="dashscope-key",
        target_model="cosyvoice-v3.5-plus",
        enrollment_service=_FakeEnrollment(),
        synthesizer_factory=lambda **kwargs: _FakeSynthesizer(**kwargs),
        request_timeout_seconds=12,
    )

    result = await provider.synthesize_speech(
        {
            "text": "hello cosyvoice",
            "voice": "cosy-voice-001",
            "task_id": "cosyunit1",
            "output_dir": str(tmp_path),
        }
    )

    audio_path = tmp_path / "cosyunit1.mp3"
    assert audio_path.read_bytes() == b"MP3"
    assert result["audio_path"] == str(audio_path)
    assert result["provider"] == "cosyvoice-tts"
    assert result["model"] == "cosyvoice-v3.5-plus"
    assert result["characters"] == len("hello cosyvoice")
    assert _FakeSynthesizer.instances[0].kwargs["model"] == "cosyvoice-v3.5-plus"
    assert _FakeSynthesizer.instances[0].kwargs["voice"] == "cosy-voice-001"
    assert _FakeSynthesizer.instances[0].calls == [
        {"text": "hello cosyvoice", "timeout_millis": 12_000}
    ]


@pytest.mark.asyncio
async def test_cosyvoice_base_url_sets_and_restores_dashscope_globals(monkeypatch):
    import dashscope

    from app.providers.voice_clone.cosyvoice import CosyVoiceCloneProvider

    monkeypatch.setattr(dashscope, "api_key", "previous-key", raising=False)
    monkeypatch.setattr(
        dashscope,
        "base_http_api_url",
        "https://dashscope.aliyuncs.com/api/v1",
        raising=False,
    )
    monkeypatch.setattr(
        dashscope,
        "base_websocket_api_url",
        "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
        raising=False,
    )
    captured = {}

    class _CapturingEnrollment(_FakeEnrollment):
        def create_voice(self, target_model: str, prefix: str, url: str) -> str:
            captured["api_key"] = dashscope.api_key
            captured["http"] = dashscope.base_http_api_url
            captured["websocket"] = dashscope.base_websocket_api_url
            return super().create_voice(target_model, prefix, url)

    provider = CosyVoiceCloneProvider(
        api_key="workspace-key",
        base_url="https://ws-es3thcbyi1dxjd5h.cn-beijing.maas.aliyuncs.com/api/v1",
        enrollment_service=_CapturingEnrollment(),
    )

    await provider.clone_voice(
        {
            "brand_voice_id": "brand-001",
            "source_audio_url": "https://storage.test/audio.wav",
        }
    )

    assert captured == {
        "api_key": "workspace-key",
        "http": "https://ws-es3thcbyi1dxjd5h.cn-beijing.maas.aliyuncs.com/api/v1",
        "websocket": (
            "wss://ws-es3thcbyi1dxjd5h.cn-beijing.maas.aliyuncs.com"
            "/api-ws/v1/inference"
        ),
    }
    assert dashscope.api_key == "previous-key"
    assert dashscope.base_http_api_url == "https://dashscope.aliyuncs.com/api/v1"
    assert (
        dashscope.base_websocket_api_url
        == "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    )


@pytest.mark.asyncio
async def test_cosyvoice_unset_base_url_leaves_dashscope_urls_unchanged(monkeypatch):
    import dashscope

    from app.providers.voice_clone.cosyvoice import CosyVoiceCloneProvider

    monkeypatch.setattr(dashscope, "base_http_api_url", "https://default-http", raising=False)
    monkeypatch.setattr(dashscope, "base_websocket_api_url", "wss://default-ws", raising=False)
    captured = {}

    class _CapturingEnrollment(_FakeEnrollment):
        def create_voice(self, target_model: str, prefix: str, url: str) -> str:
            captured["http"] = dashscope.base_http_api_url
            captured["websocket"] = dashscope.base_websocket_api_url
            return super().create_voice(target_model, prefix, url)

    provider = CosyVoiceCloneProvider(
        api_key="workspace-key",
        enrollment_service=_CapturingEnrollment(),
    )

    await provider.clone_voice(
        {
            "brand_voice_id": "brand-001",
            "source_audio_url": "https://storage.test/audio.wav",
        }
    )

    assert captured == {"http": "https://default-http", "websocket": "wss://default-ws"}
    assert dashscope.base_http_api_url == "https://default-http"
    assert dashscope.base_websocket_api_url == "wss://default-ws"


def test_cosyvoice_factory_uses_env_api_key_only(monkeypatch):
    from app.db.models import ProviderConfig
    from app.providers.voice_clone.cosyvoice import _cosyvoice_voice_clone_factory

    captured = {}

    class _Provider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("app.providers.voice_clone.cosyvoice.CosyVoiceCloneProvider", _Provider)
    monkeypatch.setattr(
        "app.providers.voice_clone.cosyvoice.settings.engine_cosyvoice_voice_clone_api_key",
        "env-key",
    )
    monkeypatch.setattr(
        "app.providers.voice_clone.cosyvoice.settings.engine_cosyvoice_voice_clone_base_url",
        "https://workspace.example/api/v1",
    )

    _cosyvoice_voice_clone_factory(
        ProviderConfig(
            tenant_id=None,
            capability="voice_clone",
            provider="cosyvoice-voice-clone",
            config={"api_key": "db-key", "target_model": "custom-model"},
            is_active=True,
        )
    )

    assert captured["api_key"] == "env-key"
    assert captured["base_url"] == "https://workspace.example/api/v1"
    assert captured["target_model"] == "custom-model"
