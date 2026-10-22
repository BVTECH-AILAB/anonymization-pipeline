"""Shared test doubles: an OCR engine that reads one fixed line, a blank PNG."""

import io
from typing import Any

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from src.config import Settings
from src.main import create_app
from src.services.words import Line

SECRET = "RSSMRA85M01H501U"  # noqa: S105 - textbook codice fiscale, not a credential
LINE = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))


class FakeEngine:
    """Reads ``SECRET`` on every image; ``fail``: raises with ``SECRET`` in it."""

    def __init__(self, ready: bool = True, fail: bool = False) -> None:
        self._ready = ready
        self.fail = fail

    @property
    def ready(self) -> bool:
        return self._ready

    def load(self) -> None:
        self._ready = True

    def predict(self, _img: np.ndarray) -> list[Line]:
        if self.fail:
            raise RuntimeError(SECRET)
        return [Line(SECRET, 0.9, LINE)]

    def recognize(self, _img: np.ndarray) -> tuple[str, float]:
        if self.fail:
            raise RuntimeError(SECRET)
        return SECRET, 0.91234


def png(w: int = 40, h: int = 20) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def client_for(engine: FakeEngine, **settings: Any) -> TestClient:
    """An app on ``engine``; no margin pass unless asked (one result per line)."""
    values: dict[str, Any] = {"margin_pt": 0} | settings
    return TestClient(create_app(Settings(**values), engine))  # type: ignore[arg-type]
