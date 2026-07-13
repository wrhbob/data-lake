"""Optional HTTP Basic Auth gate for the console.

Activated only when ``FILE_ASSET_BASIC_AUTH`` is set (format ``user:pass``); see
``app.main.create_app``. Implemented as a pure ASGI middleware so it never touches
the request body (file uploads / streaming keep working). ``/healthz`` is exempt
so simple uptime checks keep working without credentials.
"""

from __future__ import annotations

import base64
import hmac


class BasicAuthMiddleware:
    def __init__(self, app, *, username: str, password: str) -> None:
        self.app = app
        self._expected = b"Basic " + base64.b64encode(f"{username}:{password}".encode("utf-8"))

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path") or ""
        if path == "/healthz":
            return await self.app(scope, receive, send)
        auth = b""
        for key, value in scope.get("headers", ()):  # type: ignore[union-attr]
            if key == b"authorization":
                auth = value
                break
        if auth and hmac.compare_digest(auth, self._expected):
            return await self.app(scope, receive, send)
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"www-authenticate", b'Basic realm="file-asset-console", charset="UTF-8"'),
                    (b"content-type", b"text/plain; charset=utf-8"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": "Authentication required.\n".encode("utf-8")})
