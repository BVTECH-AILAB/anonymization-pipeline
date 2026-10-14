"""Helpers shared by the pipeline tests: isolated configuration and fake VLMs."""

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import yaml
from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
)
from PIL import Image

from anonymization_pipeline.extraction.native_pdf import Conversion
from anonymization_pipeline.pipeline_config import (
    PHASES,
    PipelineConfig,
    load_pipeline_config,
)
from anonymization_pipeline.vlm import VLMClient, VLMError, VLMResponse
from tests.conftest import make_settings

FIXTURES = Path(__file__).parent / "fixtures"

Answer = str | VLMResponse | Exception
Responder = Callable[[str, str, str, list[bytes], dict[str, Any] | None], Answer]


def pipeline_config(overrides: dict | None = None, **settings: Any) -> PipelineConfig:
    """Pipeline configuration from explicit settings only (no environment, no .env)."""
    return load_pipeline_config(make_settings(**settings), overrides)


def models_file(
    directory: Path,
    models: dict[str, int],
    phases: dict[str, str],
    text_only: tuple[str, ...] = (),
) -> str:
    """A models file in ``directory``: catalog name -> context, phase -> model (missing phases
    use the first model); every model sees images except the ``text_only`` ones."""
    first = next(iter(models))
    path = directory / "models.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "models": [
                    {"name": n, "context": c, "vision": n not in text_only}
                    for n, c in models.items()
                ],
                "phases": {p: phases.get(p, first) for p in PHASES},
            }
        )
    )
    return str(path)


def fake_convert(text: Callable[[int], str]) -> Callable[..., Conversion]:
    """Stand-in for ``native_pdf.convert``: page i -> ``text(i)``, with a docling document holding
    one text item per non-empty line, stacked from the top of an A4 page."""

    def convert(data: bytes, pages: list[int], c: object) -> Conversion:
        doc = DoclingDocument(name="fake")
        for i in pages:
            doc.add_page(i + 1, Size(width=595, height=842))
            top = 800.0
            for line in text(i).split("\n"):
                if line.strip():
                    doc.add_text(
                        DocItemLabel.TEXT,
                        line,
                        prov=ProvenanceItem(
                            page_no=i + 1,
                            bbox=BoundingBox(
                                l=50,
                                t=top,
                                r=545,
                                b=top - 14,
                                coord_origin=CoordOrigin.BOTTOMLEFT,
                            ),
                            charspan=(0, len(line)),
                        ),
                    )
                    top -= 20
        return Conversion({i: text(i) for i in pages}, doc)

    return convert


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

    def __init__(
        self,
        cfg: PipelineConfig,
        responder: Responder,
        available: set[str] | None = None,
    ) -> None:
        super().__init__(
            cfg, httpx.AsyncClient(transport=httpx.MockTransport(_no_network))
        )
        self.responder = responder
        self.available = available
        self.calls: list[tuple[str, str, int]] = []
        self.used_models: list[tuple[str, str]] = []  # (phase, model) of every call

    async def models_available(self) -> set[str]:
        """``available`` models; None simulates an unreachable backend."""
        if self.available is None:
            raise VLMError("backend unreachable")
        return set(self.available)

    async def call(
        self,
        phase: str,
        system: str,
        user: str,
        images: list[bytes] | None = None,
        schema: dict[str, Any] | None = None,
    ) -> VLMResponse:
        self.calls.append((phase, user, len(images or [])))
        self.used_models.append((phase, self.cfg.model_for(phase)[0]))
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
    """

    def __init__(
        self,
        values: dict[str, str],
        markdown: dict[int, str] | None = None,
        missed: tuple[str, ...] = (),
        invented: tuple[str, ...] = (),
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.values, self.markdown = values, markdown or {}
        self.missed, self.invented = set(missed), list(invented)
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
