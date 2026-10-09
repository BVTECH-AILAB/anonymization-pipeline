"""Request-id and upload-limit middleware (pure ASGI, safe with streaming responses)."""

import re
import uuid

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from anonymization_pipeline.errors import (
    LengthRequiredError,
    PayloadTooLargeError,
    ServiceError,
    error_body,
)
from anonymization_pipeline.logging_setup import request_id_var

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class RequestIdMiddleware:
    """Propagate ``X-Request-ID`` into logs and responses.

    An incoming id is reused only if it looks like an id, so a caller cannot
    inject arbitrary text (or personal data) into every log line.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = ""
        for name, value in scope.get("headers", []):
            if name.decode("latin-1").lower() == REQUEST_ID_HEADER:
                incoming = value.decode("latin-1")
                break
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


class UploadLimitMiddleware:
    """Reject request bodies over the limit before they are read.

    FastAPI reads a whole multipart body before the endpoint runs, so the limit is applied here,
    on ``Content-Length``. The restore endpoints receive two files: ``max_bytes`` is per request.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return
        length = ""
        for name, value in scope.get("headers", []):
            if name.decode("latin-1").lower() == "content-length":
                length = value.decode("latin-1")
                break
        error: ServiceError | None = None
        if not length:
            error = LengthRequiredError()
        elif not length.isdigit() or int(length) > self.max_bytes:
            error = PayloadTooLargeError()
        if error is None:
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            status_code=error.status_code, content=error_body(error)
        )
        await response(scope, receive, send)
