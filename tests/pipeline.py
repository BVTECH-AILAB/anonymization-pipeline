"""Helpers shared by the pipeline tests: isolated configuration and fake VLMs."""

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from anonymization_pipeline.pipeline_config import PipelineConfig, load_pipeline_config
from anonymization_pipeline.vlm import VLMClient, VLMResponse
from tests.conftest import make_settings

FIXTURES = Path(__file__).parent / "fixtures"

Answer = str | VLMResponse | Exception
Responder = Callable[[str, str, str, list[bytes], dict[str, Any] | None], Answer]


def pipeline_config(overrides: dict | None = None, **settings: Any) -> PipelineConfig:
    """Pipeline configuration from explicit settings only (no environment, no .env)."""
    return load_pipeline_config(make_settings(**settings), overrides)


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected HTTP call to {request.url}")


def prompt_text(user: str) -> str:
    """The document text between ``<<<`` and ``>>>`` in a detection or verification prompt."""
    return user.split("<<<\n", 1)[1].rsplit("\n>>>", 1)[0]


def schema_categories(schema: dict[str, Any]) -> list[str]:
    return schema["properties"]["entities"]["items"]["properties"]["category"]["enum"]


def width(image: bytes) -> int:
    return Image.open(io.BytesIO(image)).width


def png(image_width: int, height: int = 300) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (image_width, height), "white").save(buffer, "PNG")
    return buffer.getvalue()


class FakeVLM(VLMClient):
    """Client without network: ``responder(phase, system, user, images, schema)`` returns the answer
    text, a VLMResponse or an exception to raise."""

    kind = "fake"

    def __init__(self, cfg: PipelineConfig, responder: Responder) -> None:
        super().__init__(
            cfg, httpx.AsyncClient(transport=httpx.MockTransport(_no_network))
        )
        self.responder = responder
        self.calls: list[tuple[str, str, int]] = []

    async def call(
        self,
        phase: str,
        system: str,
        user: str,
        images: list[bytes] | None = None,
        schema: dict[str, Any] | None = None,
    ) -> VLMResponse:
        self.calls.append((phase, user, len(images or [])))
        answer = self.responder(phase, system, user, list(images or []), schema)
        if isinstance(answer, Exception):
            self.stats[phase].errors += 1
            raise answer
        response = VLMResponse(text=answer) if isinstance(answer, str) else answer
        self.stats[phase].calls += 1
        return response

    def phases(self) -> list[str]:
        return [c[0] for c in self.calls]


class IdealModel:
    """Behaves like an "ideal" VLM from a dictionary value -> category.

    extraction: ``markdown[image width]`` (every test page has a different width).
    detection/verification: the values present in the text, in the categories of the schema;
    ``missed`` values are not seen by detection (verification must find them); ``invented`` ones
    are hallucinations added to the detection answer.
    redaction: ``groups`` (lists of values) or every value on its own.
    """

    def __init__(
        self,
        values: dict[str, str],
        markdown: dict[int, str] | None = None,
        missed: tuple[str, ...] = (),
        invented: tuple[str, ...] = (),
        groups: list[list[str]] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.values, self.markdown = values, markdown or {}
        self.missed, self.invented, self.groups = (
            set(missed),
            list(invented),
            groups or [],
        )
        self.errors = errors or {}

    def __call__(
        self,
        phase: str,
        system: str,
        user: str,
        images: list[bytes],
        schema: dict[str, Any] | None,
    ) -> Answer:
        if phase in self.errors:
            return self.errors[phase]
        if phase == "extraction":
            return self.markdown[width(images[0])]
        if phase == "redaction":
            rows = [
                r.split(". ", 1)
                for r in user.split("Values:\n", 1)[1].splitlines()
                if ". [" in r
            ]
            ids = {r[1].split("] ", 1)[1]: int(r[0]) for r in rows}
            groups = [[ids[v] for v in g if v in ids] for g in self.groups]
            return json.dumps({"groups": [g for g in groups if g]})
        assert schema is not None
        text, categories = prompt_text(user), schema_categories(schema)
        out = [
            {"text": v, "category": c}
            for v, c in self.values.items()
            if c in categories
            and v in text
            and not (phase == "detection" and v in self.missed)
        ]
        if phase == "detection":
            out += [{"text": v, "category": categories[0]} for v in self.invented]
        return json.dumps({"entities": out})
