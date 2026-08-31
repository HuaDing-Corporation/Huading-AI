import threading
import time
from contextlib import contextmanager, nullcontext

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

    def list_voices(self, prefix=None, page_index=0, page_size=10):
        return []


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
async def test_cosyvoice_clone_recovers_remote_success_after_timeout_across_new_local_key(
    monkeypatch,
):
    from app.providers.voice_clone import cosyvoice as cosyvoice_module
    from app.providers.voice_clone.cosyvoice import CosyVoiceCloneProvider

    class _RemoteSuccessTimeoutEnrollment(_FakeEnrollment):
        def __init__(self) -> None:
            super().__init__()
            self.voices: list[dict[str, str]] = []

        def create_voice(self, target_model: str, prefix: str, url: str) -> str:
            self.create_calls.append(
                {"target_model": target_model, "prefix": prefix, "url": url}
            )
            self.voices.append({"voice_id": f"{prefix}-remote-001", "prefix": prefix})
            raise TimeoutError("response lost after remote commit")

        def list_voices(self, prefix=None, page_index=0, page_size=10):
            return [item for item in self.voices if item["prefix"] == prefix]

    enrollment = _RemoteSuccessTimeoutEnrollment()
    provider = CosyVoiceCloneProvider(
        api_key="dashscope-key",
        target_model="cosyvoice-v3.5-plus",
        enrollment_service=enrollment,
    )
    monkeypatch.setattr(
        cosyvoice_module,
        "_cosyvoice_recovery_claim",
        lambda **_kwargs: nullcontext(),
    )
    stable_request_key = "f" * 64
    first = await provider.clone_voice(
        {
            "brand_voice_id": "first-local-row",
            "billing_operation_id": "operation-first",
            "external_request_key": stable_request_key,
            "source_audio_url": "https://storage.test/audio.wav",
        }
    )
    second = await provider.clone_voice(
        {
            "brand_voice_id": "second-local-row-new-http-key",
            "billing_operation_id": "operation-second",
            "external_request_key": stable_request_key,
            "source_audio_url": "https://storage.test/audio.wav",
        }
    )

    assert first == second
    assert first == {
        "speaker_id": enrollment.voices[0]["voice_id"],
        "status": "ready",
        "provider": "cosyvoice-voice-clone",
    }
    assert len(enrollment.voices[0]["prefix"]) == 9
    assert enrollment.voices[0]["prefix"].isalnum()
    assert enrollment.voices[0]["prefix"].islower()
    assert len(enrollment.create_calls) == 1


@pytest.mark.asyncio
async def test_cosyvoice_external_request_key_requires_durable_operation_identity():
    from app.providers.voice_clone.cosyvoice import (
        CosyVoiceCloneError,
        CosyVoiceCloneProvider,
    )

    enrollment = _FakeEnrollment()
    provider = CosyVoiceCloneProvider(
        api_key="dashscope-key",
        enrollment_service=enrollment,
    )

    with pytest.raises(CosyVoiceCloneError):
        await provider.clone_voice(
            {
                "external_request_key": "1" * 64,
                "source_audio_url": "https://storage.test/audio.wav",
            }
        )

    assert enrollment.create_calls == []


@pytest.mark.asyncio
async def test_cosyvoice_clone_prevents_pinned_sdk_retry_after_remote_timeout(monkeypatch):
    from dashscope.audio.tts_v2 import enrollment as enrollment_module
    from dashscope.audio.tts_v2.enrollment import VoiceEnrollmentService
    from dashscope.client.base_api import BaseApi

    from app.providers.voice_clone import cosyvoice as cosyvoice_module
    from app.providers.voice_clone.cosyvoice import CosyVoiceCloneProvider

    remote_voices: list[dict[str, str]] = []
    create_attempts = 0

    class _Response:
        def __init__(self, *, output):
            self.request_id = "request-id"
            self.status_code = 200
            self.code = ""
            self.message = ""
            self.output = output

    def fake_call(cls, *, input, **_kwargs):
        nonlocal create_attempts
        if input["action"] == "list_voice":
            prefix = input.get("prefix")
            return _Response(
                output={
                    "voice_list": [
                        item for item in remote_voices if item["prefix"] == prefix
                    ]
                }
            )
        assert input["action"] == "create_voice"
        create_attempts += 1
        voice_id = f"{input['prefix']}-remote-{create_attempts}"
        remote_voices.append({"voice_id": voice_id, "prefix": input["prefix"]})
        if create_attempts == 1:
            raise TimeoutError("response lost after remote commit")
        return _Response(output={"voice_id": voice_id})

    monkeypatch.setattr(BaseApi, "call", classmethod(fake_call))
    monkeypatch.setattr(enrollment_module.time, "sleep", lambda _seconds: None)
    provider = CosyVoiceCloneProvider(
        api_key="dashscope-key",
        target_model="cosyvoice-v3.5-plus",
        enrollment_service=VoiceEnrollmentService(api_key="dashscope-key"),
    )
    monkeypatch.setattr(
        cosyvoice_module,
        "_cosyvoice_recovery_claim",
        lambda **_kwargs: nullcontext(),
    )

    result = await provider.clone_voice(
        {
            "billing_operation_id": "operation-pinned-sdk",
            "external_request_key": "1" * 64,
            "source_audio_url": "https://storage.test/audio.wav",
        }
    )

    assert result["speaker_id"] == remote_voices[0]["voice_id"]
    assert create_attempts == 1
    assert len(remote_voices) == 1
    assert len(remote_voices[0]["prefix"]) == 9
    assert remote_voices[0]["prefix"].isalnum()
    assert remote_voices[0]["prefix"].islower()


@pytest.mark.asyncio
async def test_cosyvoice_clone_does_not_reuse_different_full_hash_with_same_old_prefix(
    monkeypatch,
):
    from app.providers.voice_clone import cosyvoice as cosyvoice_module
    from app.providers.voice_clone.cosyvoice import CosyVoiceCloneProvider

    class _CollidingPrefixEnrollment(_FakeEnrollment):
        def __init__(self) -> None:
            super().__init__()
            self.voices = [
                {
                    "voice_id": "bvdeadbeef-existing-other-request",
                    "prefix": "bvdeadbeef",
                }
            ]

        def create_voice(self, target_model: str, prefix: str, url: str) -> str:
            self.create_calls.append(
                {"target_model": target_model, "prefix": prefix, "url": url}
            )
            voice_id = f"{prefix}-new-request"
            self.voices.append({"voice_id": voice_id, "prefix": prefix})
            return voice_id

        def list_voices(self, prefix=None, page_index=0, page_size=10):
            return [item for item in self.voices if item["prefix"] == prefix]

    enrollment = _CollidingPrefixEnrollment()
    provider = CosyVoiceCloneProvider(
        api_key="dashscope-key",
        enrollment_service=enrollment,
    )
    monkeypatch.setattr(
        cosyvoice_module,
        "_cosyvoice_recovery_claim",
        lambda **_kwargs: nullcontext(),
    )

    result = await provider.clone_voice(
        {
            "billing_operation_id": "operation-old-prefix",
            "external_request_key": "deadbeef" + "2" * 56,
            "source_audio_url": "https://storage.test/audio.wav",
        }
    )

    request_prefix = enrollment.create_calls[0]["prefix"]
    assert result["speaker_id"] == f"{request_prefix}-new-request"
    assert request_prefix != "bvdeadbeef"
    assert len(request_prefix) == 9
    assert request_prefix.isalnum()
    assert request_prefix.islower()
    assert len(enrollment.create_calls) == 1


def test_cosyvoice_timeout_recovery_never_cross_binds_concurrent_request_hashes(
    monkeypatch,
):
    from app.providers.voice_clone import cosyvoice as cosyvoice_module
    from app.providers.voice_clone.cosyvoice import CosyVoiceCloneProvider

    class _SharedRemoteSupplier:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.voices: list[dict[str, str]] = []
            self.a_create_started = threading.Event()
            self.b_create_finished = threading.Event()
            self.b_voice_id = ""
            self.create_prefixes: dict[str, str] = {}

        def list_voices(self, prefix: str) -> list[dict[str, str]]:
            with self._lock:
                return [item.copy() for item in self.voices if item["prefix"] == prefix]

        def create_voice(self, request: str, prefix: str) -> str:
            self.create_prefixes[request] = prefix
            if request == "a":
                self.a_create_started.set()
                assert self.b_create_finished.wait(2)
                raise TimeoutError("A response was lost without a remote commit")

            assert self.a_create_started.wait(2)
            voice_id = f"{prefix}-speaker-b"
            with self._lock:
                self.voices.append({"voice_id": voice_id, "prefix": prefix})
                self.b_voice_id = voice_id
            self.b_create_finished.set()
            return voice_id

    class _IndependentEnrollment:
        def __init__(self, remote: _SharedRemoteSupplier, request: str) -> None:
            self.remote = remote
            self.request = request

        def create_voice(self, target_model: str, prefix: str, url: str) -> str:
            return self.remote.create_voice(self.request, prefix)

        def list_voices(self, prefix=None, page_index=0, page_size=10):
            return self.remote.list_voices(prefix)

    remote = _SharedRemoteSupplier()
    provider_a = CosyVoiceCloneProvider(
        api_key="dashscope-key-a",
        enrollment_service=_IndependentEnrollment(remote, "a"),
    )
    provider_b = CosyVoiceCloneProvider(
        api_key="dashscope-key-b",
        enrollment_service=_IndependentEnrollment(remote, "b"),
    )
    monkeypatch.setattr(
        cosyvoice_module,
        "_dashscope_runtime",
        lambda *_args: nullcontext(),
    )
    monkeypatch.setattr(
        cosyvoice_module,
        "_cosyvoice_recovery_claim",
        lambda **_kwargs: nullcontext(),
    )
    payloads = {
        "a": {
            "billing_operation_id": "operation-a",
            "external_request_key": "deadbeef" + "a" * 56,
            "source_audio_url": "https://storage.test/a.wav",
        },
        "b": {
            "billing_operation_id": "operation-b",
            "external_request_key": "deadbeef" + "b" * 56,
            "source_audio_url": "https://storage.test/b.wav",
        },
    }
    results: dict[str, dict[str, str]] = {}
    errors: dict[str, BaseException] = {}

    def run_clone(request: str, provider: CosyVoiceCloneProvider) -> None:
        try:
            results[request] = provider.clone_voice_sync(payloads[request])
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors[request] = exc

    thread_a = threading.Thread(target=run_clone, args=("a", provider_a))
    thread_b = threading.Thread(target=run_clone, args=("b", provider_b))
    thread_a.start()
    assert remote.a_create_started.wait(2)
    thread_b.start()
    thread_a.join(2)
    thread_b.join(2)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert "b" not in errors
    assert results["b"]["speaker_id"] == remote.b_voice_id
    assert remote.create_prefixes["a"] != remote.create_prefixes["b"]
    assert all(len(prefix) == 9 for prefix in remote.create_prefixes.values())
    assert isinstance(errors.get("a"), TimeoutError)
    assert results.get("a", {}).get("speaker_id") != remote.b_voice_id
    with pytest.raises(TimeoutError):
        provider_a.clone_voice_sync(payloads["a"])


def test_cosyvoice_timeout_recovery_fails_closed_on_true_marker_collision_across_instances(
    monkeypatch,
):
    from app.providers.voice_clone import cosyvoice as cosyvoice_module
    from app.providers.voice_clone.cosyvoice import (
        CosyVoiceCloneError,
        CosyVoiceCloneProvider,
    )

    class _SharedRemoteSupplier:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.voices: list[dict[str, str]] = []
            self.a_create_started = threading.Event()
            self.b_outcome_known = threading.Event()
            self.create_requests: list[str] = []

        def list_voices(self, prefix: str) -> list[dict[str, str]]:
            with self._lock:
                return [item.copy() for item in self.voices if item["prefix"] == prefix]

        def create_voice(self, request: str, prefix: str) -> str:
            self.create_requests.append(request)
            if request == "a":
                self.a_create_started.set()
                assert self.b_outcome_known.wait(2)
                raise TimeoutError("A response was lost without a remote commit")

            voice_id = f"{prefix}-speaker-b"
            with self._lock:
                self.voices.append({"voice_id": voice_id, "prefix": prefix})
            self.b_outcome_known.set()
            return voice_id

    class _IndependentEnrollment:
        def __init__(self, remote: _SharedRemoteSupplier, request: str) -> None:
            self.remote = remote
            self.request = request

        def create_voice(self, target_model: str, prefix: str, url: str) -> str:
            return self.remote.create_voice(self.request, prefix)

        def list_voices(self, prefix=None, page_index=0, page_size=10):
            return self.remote.list_voices(prefix)

    remote = _SharedRemoteSupplier()
    claims: list[tuple[str, str, str]] = []

    @contextmanager
    def durable_collision_claim(*, operation_id: str, request_key: str, marker: str):
        claims.append((operation_id, request_key, marker))
        if operation_id == "operation-b":
            remote.b_outcome_known.set()
            raise CosyVoiceCloneError("CosyVoice recovery marker collision.")
        yield

    # Force the finite supplier namespace to produce the exact same complete
    # nine-character marker for two different full request hashes.
    monkeypatch.setattr(
        cosyvoice_module,
        "_request_recovery_marker",
        lambda _request_key: "collision",
    )
    monkeypatch.setattr(
        cosyvoice_module,
        "_cosyvoice_recovery_claim",
        durable_collision_claim,
        raising=False,
    )
    monkeypatch.setattr(
        cosyvoice_module,
        "_dashscope_runtime",
        lambda *_args: nullcontext(),
    )
    provider_a = CosyVoiceCloneProvider(
        api_key="dashscope-key-a",
        enrollment_service=_IndependentEnrollment(remote, "a"),
    )
    provider_b = CosyVoiceCloneProvider(
        api_key="dashscope-key-b",
        enrollment_service=_IndependentEnrollment(remote, "b"),
    )
    payloads = {
        "a": {
            "billing_operation_id": "operation-a",
            "external_request_key": "1" * 64,
            "source_audio_url": "https://storage.test/a.wav",
        },
        "b": {
            "billing_operation_id": "operation-b",
            "external_request_key": "2" * 64,
            "source_audio_url": "https://storage.test/b.wav",
        },
    }
    results: dict[str, dict[str, str]] = {}
    errors: dict[str, BaseException] = {}

    def run_clone(request: str, provider: CosyVoiceCloneProvider) -> None:
        try:
            results[request] = provider.clone_voice_sync(payloads[request])
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors[request] = exc

    thread_a = threading.Thread(target=run_clone, args=("a", provider_a))
    thread_b = threading.Thread(target=run_clone, args=("b", provider_b))
    thread_a.start()
    assert remote.a_create_started.wait(2)
    thread_b.start()
    thread_a.join(2)
    thread_b.join(2)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert results == {}
    assert isinstance(errors.get("a"), TimeoutError)
    assert isinstance(errors.get("b"), CosyVoiceCloneError)
    assert remote.voices == []
    assert remote.create_requests == ["a"]
    assert claims == [
        ("operation-a", "1" * 64, "collision"),
        ("operation-b", "2" * 64, "collision"),
    ]


def test_cosyvoice_recovery_collision_decision_fails_closed_for_one_foreign_candidate():
    from app.services.cosyvoice_recovery import (
        CosyVoiceRecoveryMarkerCollisionError,
        assert_cosyvoice_recovery_marker_owned,
    )

    request_a = "1" * 64
    request_b = "2" * 64

    with pytest.raises(CosyVoiceRecoveryMarkerCollisionError):
        assert_cosyvoice_recovery_marker_owned(
            request_key=request_a,
            marker="collision",
            candidate_request_keys=(request_a, request_b),
            marker_for_request=lambda _request_key: "collision",
        )

    assert_cosyvoice_recovery_marker_owned(
        request_key=request_a,
        marker="collision",
        candidate_request_keys=(request_a, request_a),
        marker_for_request=lambda _request_key: "collision",
    )


def test_cosyvoice_recovery_gate_uses_postgresql_transaction_advisory_lock():
    from app.services.cosyvoice_recovery import lock_cosyvoice_recovery_marker

    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

    class _Database:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, int]]] = []

        def get_bind(self):
            return _Bind()

        def execute(self, statement, params):
            self.calls.append((str(statement), params))

    database = _Database()

    lock_cosyvoice_recovery_marker(database, marker="collision")

    assert len(database.calls) == 1
    statement, params = database.calls[0]
    assert statement == "SELECT pg_advisory_xact_lock(:lock_id)"
    assert isinstance(params["lock_id"], int)


def test_cosyvoice_recovery_claim_checks_durable_operation_scope_before_supplier_call():
    from app.services.cosyvoice_recovery import (
        CosyVoiceRecoveryMarkerCollisionError,
        claim_cosyvoice_recovery_marker,
    )

    class _Dialect:
        name = "sqlite"

    class _Bind:
        dialect = _Dialect()

    class _Database:
        def __init__(self) -> None:
            self.rolled_back = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get_bind(self):
            return _Bind()

        def scalar(self, _statement):
            return "1" * 64

        def scalars(self, _statement):
            return iter(("1" * 64, "2" * 64))

        def rollback(self):
            self.rolled_back = True

    database = _Database()

    with pytest.raises(CosyVoiceRecoveryMarkerCollisionError):
        with claim_cosyvoice_recovery_marker(
            session_factory=lambda: database,
            operation_id="operation-a",
            request_key="1" * 64,
            marker="collision",
            marker_for_request=lambda _request_key: "collision",
        ):
            pytest.fail("supplier call must not run for a colliding durable request")

    assert database.rolled_back is True


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


def test_cosyvoice_dashscope_runtime_serializes_global_url_changes(monkeypatch):
    import dashscope

    from app.providers.voice_clone.cosyvoice import CosyVoiceCloneProvider

    monkeypatch.setattr(dashscope, "api_key", "default-key", raising=False)
    monkeypatch.setattr(dashscope, "base_http_api_url", "https://default-http", raising=False)
    monkeypatch.setattr(dashscope, "base_websocket_api_url", "wss://default-ws", raising=False)
    first_inside = threading.Event()
    release_first = threading.Event()
    captures: list[tuple[str, str, str]] = []
    errors: list[BaseException] = []

    class _SlowEnrollment(_FakeEnrollment):
        def create_voice(self, target_model: str, prefix: str, url: str) -> str:
            captures.append(("first-start", dashscope.api_key, dashscope.base_http_api_url))
            first_inside.set()
            assert release_first.wait(2)
            captures.append(("first-end", dashscope.api_key, dashscope.base_http_api_url))
            return "cosy-first"

    class _FastEnrollment(_FakeEnrollment):
        def create_voice(self, target_model: str, prefix: str, url: str) -> str:
            captures.append(("second", dashscope.api_key, dashscope.base_http_api_url))
            return "cosy-second"

    first = CosyVoiceCloneProvider(
        api_key="workspace-key-1",
        base_url="https://workspace-one.example/api/v1",
        enrollment_service=_SlowEnrollment(),
    )
    second = CosyVoiceCloneProvider(
        api_key="workspace-key-2",
        base_url="https://workspace-two.example/api/v1",
        enrollment_service=_FastEnrollment(),
    )

    def run_clone(provider: CosyVoiceCloneProvider, brand_voice_id: str) -> None:
        try:
            provider.clone_voice_sync(
                {
                    "brand_voice_id": brand_voice_id,
                    "source_audio_url": "https://storage.test/audio.wav",
                }
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    first_thread = threading.Thread(target=run_clone, args=(first, "brand-one"))
    second_thread = threading.Thread(target=run_clone, args=(second, "brand-two"))
    first_thread.start()
    assert first_inside.wait(2)
    second_thread.start()
    time.sleep(0.05)
    release_first.set()
    first_thread.join(2)
    second_thread.join(2)

    assert errors == []
    assert captures == [
        ("first-start", "workspace-key-1", "https://workspace-one.example/api/v1"),
        ("first-end", "workspace-key-1", "https://workspace-one.example/api/v1"),
        ("second", "workspace-key-2", "https://workspace-two.example/api/v1"),
    ]
    assert dashscope.api_key == "default-key"
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
