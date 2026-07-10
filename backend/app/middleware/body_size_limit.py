from __future__ import annotations

import json
from collections.abc import Mapping

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
        path_limits: Mapping[str, int] | None = None,
    ) -> None:
        self.app = app
        self.max_body_size = max_body_size
        self.paths = paths
        self.path_limits = dict(path_limits or {})

    def _body_limit(self, scope: Scope) -> int | None:
        if scope["type"] != "http":
            return None
        if scope.get("method") not in {"POST", "PUT", "PATCH"}:
            return None
        path = str(scope.get("path") or "")
        for item, limit in sorted(
            self.path_limits.items(),
            key=lambda pair: len(pair[0]),
            reverse=True,
        ):
            if path == item or path.startswith(f"{item}/"):
                return limit
        if any(path == item or path.startswith(f"{item}/") for item in self.paths):
            return self.max_body_size
        return None

    def _content_length(self, scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    return int(value.decode("latin1"))
                except ValueError:
                    return None
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        body_limit = self._body_limit(scope)
        if body_limit is None:
            await self.app(scope, receive, send)
            return

        content_length = self._content_length(scope)
        if content_length is not None and content_length > body_limit:
            await self._send_413(send, max_body_size=body_limit)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > body_limit:
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
                await self._send_413(send, max_body_size=body_limit)

    async def _send_413(self, send: Send, *, max_body_size: int) -> None:
        payload = {
            "data": None,
            "error": {
                "code": "REQUEST_BODY_TOO_LARGE",
                "message": f"Request body too large; limit is {max_body_size} bytes.",
                "detail": {"limit": max_body_size},
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
