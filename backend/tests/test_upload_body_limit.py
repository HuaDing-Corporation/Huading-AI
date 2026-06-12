import pytest
from fastapi.testclient import TestClient

from app.api.v1.routes import uploads as uploads_route
from app.main import app
from app.middleware.body_size_limit import BodySizeLimitMiddleware


def test_upload_content_length_rejected_before_auth_and_handler() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/v1/uploads",
        files={
            "file": (
                "huge.png",
                b"0" * (uploads_route._MAX_BYTES + 1),
                "image/png",
            )
        },
    )

    assert response.status_code == 413
    body = response.json()
    assert body["error"]["code"] == "REQUEST_BODY_TOO_LARGE"


@pytest.mark.asyncio
async def test_upload_chunked_body_limit_stops_receive_stream() -> None:
    calls = {"receive": 0, "inner_completed": False}

    async def inner_app(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] == "http.request" and not message.get("more_body", False):
                break
        calls["inner_completed"] = True
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = BodySizeLimitMiddleware(
        inner_app,
        max_body_size=uploads_route._MAX_BYTES,
        paths=("/api/v1/uploads",),
    )
    messages = [
        {"type": "http.request", "body": b"0" * uploads_route._MAX_BYTES, "more_body": True},
        {"type": "http.request", "body": b"x", "more_body": True},
        {"type": "http.request", "body": b"after-limit", "more_body": False},
    ]

    async def receive():
        calls["receive"] += 1
        return messages.pop(0)

    sent = []

    async def send(message):
        sent.append(message)

    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/uploads",
            "headers": [(b"content-type", b"multipart/form-data; boundary=x")],
        },
        receive,
        send,
    )

    assert calls["receive"] == 2
    assert calls["inner_completed"] is False
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
