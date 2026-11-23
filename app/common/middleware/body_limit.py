"""Reject oversized request bodies *before* they are parsed or buffered.

FastAPI parses multipart bodies before dependencies/handlers run, so a size check inside a
handler is too late.  This pure-ASGI middleware enforces a per-route ceiling using the
Content-Length header and, for chunked uploads, by counting streamed bytes.
"""

import re
from typing import Any

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_MB = 1024 * 1024
# (regex, max body bytes) — first match wins. Limits include multipart overhead.
BODY_LIMITS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^/api/candidate/submission/[^/]+/screenshot$"), 3 * _MB),
    (re.compile(r"^/api/candidate/submission/[^/]+/malpractice$"), 6 * _MB),
    (re.compile(r"^/api/candidate/submission/[^/]+/malpractice/\d+/media$"), 62 * _MB),
    (re.compile(r"^/api/questions/categories/[^/]+/images$"), 6 * _MB),
    (re.compile(r"^/api/questions/categories/[^/]+/excel-import$"), 12 * _MB),
]
DEFAULT_JSON_LIMIT = 2 * _MB  # every other request body


def limit_for(path: str) -> int:
    for pattern, limit in BODY_LIMITS:
        if pattern.match(path):
            return limit
    return DEFAULT_JSON_LIMIT


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        limit = limit_for(scope["path"])
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    await self._reject(scope, receive, send, limit)
                    return
            except ValueError:
                await self._reject(scope, receive, send, limit)
                return

        received = 0

        async def counting_receive() -> Any:
            # Chunked uploads carry no Content-Length: count the bytes as they stream in and
            # abort (HTTP 413 via FastAPI's HTTPException) the moment the limit is crossed.
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(status_code=413, detail="Request body too large")
            return message

        await self.app(scope, counting_receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "success": False,
                "message": "Request body too large",
                "data": None,
                "detail": f"Maximum allowed size is {limit // _MB} MB",
            },
        )
        await response(scope, receive, send)
