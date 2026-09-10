from io import BytesIO
from random import Random
from tempfile import TemporaryFile

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.deps import get_object_storage
from app.api.v1.routes import uploads
from app.core.config import Settings, settings
from app.db.models import Asset, User
from app.main import app, create_app
from app.services.batches import download_image_url_to_asset

MIB = 1024 * 1024


class CountingStorage:
    bucket = "upload-test"

    def __init__(self):
        self.saved = {}

    def put_bytes(self, key, content, *, content_type):
        self.saved[key] = (len(content), content_type)
        return f"memory://{key}"


@pytest.fixture
def upload_app(auth_context):
    storage = CountingStorage()
    application = create_app()
    application.dependency_overrides.update(app.dependency_overrides)
    application.dependency_overrides[get_object_storage] = lambda: storage
    return application, storage


@pytest.mark.parametrize("path", ["/api/v1/uploads", "/api/v1/uploads/images"])
@pytest.mark.parametrize("extra", [0, 1])
def test_real_multipart_image_30_mib_boundary(upload_app, auth_context, auth_db, path, extra):
    application, storage = upload_app
    size = 30 * MIB + extra
    with TemporaryFile() as source:
        for _ in range(30 * 16):
            source.write(b"p" * (64 * 1024))
        source.write(b"x" * extra)
        source.seek(0)
        response = TestClient(application).post(
            path,
            files={"file": ("synthetic.png", source, "image/png")},
            headers=auth_context["headers"],
        )
    if extra:
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
        assert storage.saved == {}
        with auth_db() as db:
            assert db.scalar(select(func.count()).select_from(Asset)) == 0
    else:
        assert response.status_code == 201, response.text
        assert list(storage.saved.values()) == [(size, "image/png")]
        key = next(iter(storage.saved))
        assert key.startswith(f"tenants/{auth_context['tenant_id']}/uploads/")
        if path.endswith("/images"):
            with auth_db() as db:
                asset = db.get(Asset, response.json()["data"]["asset_id"])
                assert asset.size_bytes == size
                assert asset.tenant_id == auth_context["tenant_id"]


@pytest.mark.parametrize(
    "path,limit",
    [
        ("/api/v1/uploads", 31 * MIB),
        ("/api/v1/uploads/", 31 * MIB),
        ("/api/v1/uploads/images", 31 * MIB),
        ("/api/v1/uploads/audio", 11 * MIB),
        ("/api/v1/uploads/videos?purpose=reverse_prompt", 201 * MIB),
        ("/api/v1/uploads/videos/?purpose=avatar_source", 201 * MIB),
        ("/api/v1/uploads/videos-extra", 10 * MIB),
        ("/api/v1/uploads/videos/extra", 10 * MIB),
        ("/api/v1/uploads/images/extra", 10 * MIB),
    ],
)
def test_preparse_body_limit_is_endpoint_specific(upload_app, path, limit):
    application, storage = upload_app
    response = TestClient(application).post(
        path,
        content=b"",
        headers={"content-length": str(limit + 1)},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_BODY_TOO_LARGE"
    assert response.json()["error"]["detail"]["limit"] == limit
    assert storage.saved == {}


@pytest.mark.parametrize("declared_length", [None, "not-a-number", "1"])
@pytest.mark.asyncio
async def test_multipart_without_valid_length_is_still_limited(
    monkeypatch, upload_app, auth_context, declared_length
):
    from starlette import formparsers

    application, storage = upload_app
    consumed = []
    spooled = []
    original = formparsers.SpooledTemporaryFile

    def track_spool(*args, **kwargs):
        result = original(*args, **kwargs)
        spooled.append(result)
        return result

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", track_spool)

    async def stream():
        yield (
            b'--boundary\r\nContent-Disposition: form-data; name="file"; '
            b'filename="image.png"\r\nContent-Type: image/png\r\n\r\n'
        )
        for index in range(34):
            consumed.append(index)
            yield b"p" * MIB
        yield b"\r\n--boundary--\r\n"

    headers = {**auth_context["headers"], "content-type": "multipart/form-data; boundary=boundary"}
    if declared_length is not None:
        headers["content-length"] = declared_length
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/uploads/images", content=stream(), headers=headers)
    assert response.status_code == 413
    assert response.json()["error"]["detail"]["limit"] == 31 * MIB
    assert len(consumed) == 31
    assert storage.saved == {}
    assert spooled and all(file.closed for file in spooled)


@pytest.mark.parametrize("path", ["/api/v1/uploads", "/api/v1/uploads/images"])
def test_image_limit_override_does_not_raise_audio_limit(monkeypatch, auth_context, path):
    from app import main

    monkeypatch.setenv("UPLOAD_IMAGE_MAX_BYTES", "16")
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "8")
    configured = Settings(_env_file=None)
    monkeypatch.setattr(main, "settings", configured)
    monkeypatch.setattr(uploads, "settings", configured)
    application = create_app()
    application.dependency_overrides.update(app.dependency_overrides)
    storage = CountingStorage()
    application.dependency_overrides[get_object_storage] = lambda: storage
    client = TestClient(application)
    image = client.post(
        path,
        files={"file": ("i.png", b"p" * 16, "image/png")},
        headers=auth_context["headers"],
    )
    image_over = client.post(
        path,
        files={"file": ("i.png", b"p" * 17, "image/png")},
        headers=auth_context["headers"],
    )
    audio_over = client.post(
        "/api/v1/uploads/audio",
        files={"file": ("a.wav", b"p" * 9, "audio/wav")},
        headers=auth_context["headers"],
    )
    assert image.status_code == 201
    assert image_over.status_code == audio_over.status_code == 413
    assert image_over.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
    assert audio_over.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
    assert list(storage.saved.values()) == [(16, "image/png")]
    body_over = client.post(path, content=b"", headers={"content-length": str(16 + MIB + 1)})
    assert body_over.status_code == 413
    assert body_over.json()["error"]["detail"]["limit"] == 16 + MIB


@pytest.mark.parametrize("extra", [0, 1])
def test_audio_real_10_mib_boundary(monkeypatch, upload_app, auth_context, extra):
    application, storage = upload_app
    monkeypatch.setattr(uploads, "_probe_audio_duration_ms", lambda *args, **kwargs: 5000)
    response = TestClient(application).post(
        "/api/v1/uploads/audio",
        files={"file": ("audio.wav", b"p" * (10 * MIB + extra), "audio/wav")},
        headers=auth_context["headers"],
    )
    if extra:
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
        assert storage.saved == {}
    else:
        assert response.status_code == 201
        assert list(storage.saved.values()) == [(10 * MIB, "audio/wav")]


@pytest.mark.parametrize("path", ["/api/v1/uploads", "/api/v1/uploads/images"])
def test_image_upload_auth_and_rbac_unchanged(upload_app, auth_context, auth_db, path):
    application, storage = upload_app
    client = TestClient(application)
    assert client.post(path, files={"file": ("i.png", b"p", "image/png")}).status_code == 401
    with auth_db() as db:
        db.get(User, auth_context["user_id"]).role = "reviewer"
        db.commit()
    response = client.post(
        path, files={"file": ("i.png", b"p", "image/png")}, headers=auth_context["headers"]
    )
    assert response.status_code == 403
    assert storage.saved == {}


@pytest.mark.parametrize("extra", [0, 1])
@pytest.mark.parametrize("declared_length", [None, "1"])
def test_batch_download_uses_30_mib_image_limit(
    monkeypatch, auth_context, auth_db, extra, declared_length
):
    from app.services import batches

    class Response:
        def __init__(self):
            self.headers = {"content-type": "image/png"}
            if declared_length is not None:
                self.headers["content-length"] = declared_length
            self.closed = False
            self.read_past_limit = False

        def iter_content(self, chunk_size):
            for _ in range(30 * MIB // chunk_size):
                yield b"p" * chunk_size
            if extra:
                yield b"x"
                self.read_past_limit = True
                yield b"must not be read"

        def close(self):
            self.closed = True

    response = Response()
    monkeypatch.setattr(batches, "_get_public_image_response", lambda url: (url, response))
    storage = CountingStorage()
    with auth_db() as db:
        if extra:
            with pytest.raises(batches.BatchImageDownloadError):
                download_image_url_to_asset(
                    db,
                    tenant_id=auth_context["tenant_id"],
                    image_url="https://images.example/i.png",
                    storage=storage,
                )
            assert storage.saved == {}
        else:
            asset = download_image_url_to_asset(
                db,
                tenant_id=auth_context["tenant_id"],
                image_url="https://images.example/i.png",
                storage=storage,
            )
            assert asset.size_bytes == 31_457_280
            assert asset.tenant_id == auth_context["tenant_id"]
            assert storage.saved[asset.storage_key] == (31_457_280, "image/png")
    assert response.closed
    assert not response.read_past_limit


@pytest.mark.parametrize("limit", [0, -1])
def test_image_limit_must_be_positive(limit):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, upload_image_max_bytes=limit)


def test_batch_image_limit_reads_independent_environment_setting(
    monkeypatch, auth_context, auth_db
):
    from app.services import batches

    monkeypatch.setenv("UPLOAD_IMAGE_MAX_BYTES", "16")
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "8")
    monkeypatch.setattr(batches, "settings", Settings(_env_file=None))
    closed = []

    class Response:
        headers = {"content-type": "image/png"}

        def __init__(self, size):
            self.size = size

        def iter_content(self, chunk_size):
            yield b"p" * self.size

        def close(self):
            closed.append(self.size)

    storage = CountingStorage()
    with auth_db() as db:
        for size in (16, 17):
            response = Response(size)
            monkeypatch.setattr(
                batches,
                "_get_public_image_response",
                lambda url, response=response: (url, response),
            )
            if size == 16:
                asset = download_image_url_to_asset(
                    db,
                    tenant_id=auth_context["tenant_id"],
                    image_url="https://images.example/i.png",
                    storage=storage,
                )
                assert asset.size_bytes == 16
            else:
                with pytest.raises(batches.BatchImageDownloadError):
                    download_image_url_to_asset(
                        db,
                        tenant_id=auth_context["tenant_id"],
                        image_url="https://images.example/i.png",
                        storage=storage,
                    )
    assert closed == [16, 17]
    assert list(storage.saved.values()) == [(16, "image/png")]


def test_decodable_image_above_14_mib_keeps_provider_compression_guard():
    from app.workers.image_gen import _apimart_safe_input_image_bytes

    pixels = Random(20260910).randbytes(2304 * 2304 * 3)
    with Image.frombytes("RGB", (2304, 2304), pixels) as source:
        buffer = BytesIO()
        source.save(buffer, format="PNG", compress_level=0)
    content = buffer.getvalue()
    assert 14 * MIB < len(content) <= 30 * MIB
    compressed, mime = _apimart_safe_input_image_bytes(content, "image/png")
    assert mime == "image/jpeg"
    assert len(compressed) <= 14 * MIB
    with Image.open(BytesIO(compressed)) as result:
        assert max(result.size) <= 2048
        result.verify()


def test_legacy_environment_does_not_override_image_default(monkeypatch):
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "10485760")
    monkeypatch.delenv("UPLOAD_IMAGE_MAX_BYTES", raising=False)
    monkeypatch.delenv("UPLOAD_VIDEO_MAX_BYTES", raising=False)
    configured = Settings(_env_file=None)
    assert configured.upload_max_bytes == 10_485_760
    assert configured.upload_image_max_bytes == 31_457_280
    assert configured.upload_video_max_bytes == 209_715_200


@pytest.mark.parametrize("extra", [0, 1])
@pytest.mark.asyncio
async def test_real_multipart_image_body_margin_boundary(upload_app, auth_context, extra):
    application, storage = upload_app
    prefix = (
        b'--boundary\r\nContent-Disposition: form-data; name="file"; '
        b'filename="image.png"\r\nContent-Type: image/png\r\n\r\n'
    )
    field = b'\r\n--boundary\r\nContent-Disposition: form-data; name="padding"\r\n\r\n'
    suffix = b"\r\n--boundary--\r\n"

    async def stream():
        yield prefix
        for _ in range(30):
            yield b"p" * MIB
        yield field
        yield b" " * (MIB - len(prefix) - len(field) - len(suffix) + extra)
        yield suffix

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/uploads/images",
            content=stream(),
            headers={
                **auth_context["headers"],
                "content-type": "multipart/form-data; boundary=boundary",
            },
        )
    if extra:
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "REQUEST_BODY_TOO_LARGE"
        assert storage.saved == {}
    else:
        assert response.status_code == 201, response.text
        assert list(storage.saved.values()) == [(31_457_280, "image/png")]


def test_malformed_multipart_never_stores_or_returns_500(upload_app, auth_context):
    application, storage = upload_app
    response = TestClient(application).post(
        "/api/v1/uploads/images",
        content=b"not multipart",
        headers={**auth_context["headers"], "content-type": "multipart/form-data"},
    )
    assert response.status_code == 400
    assert storage.saved == {}


@pytest.mark.parametrize(
    "purpose,size",
    [
        ("avatar_source", 209_715_200),
        ("reverse_prompt", 209_715_200),
        ("video_gen_reference", 104_857_600),
    ],
)
@pytest.mark.parametrize("extra", [0, 1])
@pytest.mark.asyncio
async def test_real_video_multipart_file_boundaries(
    monkeypatch, upload_app, auth_context, purpose, size, extra
):
    from app.api.v1.routes.videos import _AvatarVideoProbe
    from app.services.video_reference import NormalizedVideoReference

    application, storage = upload_app
    monkeypatch.setattr(
        uploads,
        "_probe_avatar_video_bytes",
        lambda *args, **kwargs: _AvatarVideoProbe(5000, 720, 1280, "mp4", "h264", "aac"),
    )
    monkeypatch.setattr(
        uploads,
        "normalize_video_reference",
        lambda content, **kwargs: NormalizedVideoReference(
            content, 5000, 720, 1280, "mp4", "h264", "aac", True
        ),
    )

    async def stream():
        yield (
            b'--boundary\r\nContent-Disposition: form-data; name="file"; '
            b'filename="video.mp4"\r\nContent-Type: video/mp4\r\n\r\n'
        )
        for _ in range(size // MIB):
            yield b"v" * MIB
        yield b"x" * extra
        yield b"\r\n--boundary--\r\n"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/v1/uploads/videos?purpose={purpose}",
            content=stream(),
            headers={
                **auth_context["headers"],
                "content-type": "multipart/form-data; boundary=boundary",
            },
        )
    if extra:
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
        assert storage.saved == {}
    else:
        assert response.status_code == 201, response.text
        assert list(storage.saved.values()) == [(size, "video/mp4")]


@pytest.mark.parametrize(
    "purpose,limit", [("avatar_source", 16), ("reverse_prompt", 16), ("video_gen_reference", 8)]
)
@pytest.mark.parametrize("extra", [0, 1])
def test_video_file_limits_remain_purpose_specific(
    monkeypatch, auth_context, purpose, limit, extra
):
    from app.api.v1.routes.videos import _AvatarVideoProbe
    from app.services.video_reference import NormalizedVideoReference

    monkeypatch.setattr(settings, "upload_video_max_bytes", 16)
    monkeypatch.setattr(uploads, "VIDEO_REFERENCE_MAX_BYTES", 8)
    monkeypatch.setattr(
        uploads,
        "_probe_avatar_video_bytes",
        lambda *args, **kwargs: _AvatarVideoProbe(5000, 720, 1280, "mp4", "h264", "aac"),
    )
    monkeypatch.setattr(
        uploads,
        "normalize_video_reference",
        lambda content, **kwargs: NormalizedVideoReference(
            content, 5000, 720, 1280, "mp4", "h264", "aac", True
        ),
    )
    application = create_app()
    application.dependency_overrides.update(app.dependency_overrides)
    storage = CountingStorage()
    application.dependency_overrides[get_object_storage] = lambda: storage
    response = TestClient(application).post(
        f"/api/v1/uploads/videos?purpose={purpose}",
        files={"file": ("v.mp4", b"v" * (limit + extra), "video/mp4")},
        headers=auth_context["headers"],
    )
    if extra:
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
        assert storage.saved == {}
    else:
        assert response.status_code == 201, response.text
        assert list(storage.saved.values()) == [(limit, "video/mp4")]
