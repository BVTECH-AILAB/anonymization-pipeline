"""Startup: the models load before the first request (``/health`` 503 until then)."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Load the engine on ``app.state`` unless it is loaded already."""
    engine = app.state.engine
    if not engine.ready:
        await anyio.to_thread.run_sync(engine.load)
    yield
