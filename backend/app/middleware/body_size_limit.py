from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodyTooLargeError(Exception):
    pass


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before FastAPI parses multipart uploads."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_size: int,
        paths: tuple[str, ...],
    ) -> None:
        self.app = app
        self.max_body_size = max_body_size
        self.paths = paths

    def _limited_scope(self, scope: Scope) -> bool:
        if scope["type"] != "http":
            return False
        if scope.get("method") not in {"POST", "PUT", "PATCH"}:
            return False
        path = str(scope.get("path") or "")
        return any(path == item or path.startswith(f"{item}/") for item in self.paths)

    def _content_length(self, scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    return int(value.decode("latin1"))
                except ValueError:
                    return None
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._limited_scope(scope):
            await self.app(scope, receive, send)
            return

        content_length = self._content_length(scope)
        if content_length is not None and content_length > self.max_body_size:
            await self._send_413(send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_size:
                    raise BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLargeError:
            if not response_started:
                await self._send_413(send)

    async def _send_413(self, send: Send) -> None:
        payload = {
            "data": None,
            "error": {
                "code": "REQUEST_BODY_TOO_LARGE",
                "message": f"Request body too large; limit is {self.max_body_size} bytes.",
                "detail": {"limit": self.max_body_size},
            },
            "request_id": None,
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send({"type": "http.response.body", "body": body})
