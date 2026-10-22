"""Server-Sent Events: named frames, as the document agent of ai-platform-v2 sends them."""

from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse

KEEPALIVE = ": keep-alive\n\n"


def format_sse(event: str, data: str) -> str:
    """One frame: ``event: <name>`` and a JSON ``data`` line."""
    return f"event: {event}\ndata: {data}\n\n"


def sse_response(frames: AsyncIterator[str]) -> StreamingResponse:
    """A streaming response of SSE frames, never buffered by a proxy."""
    return StreamingResponse(
        frames,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
