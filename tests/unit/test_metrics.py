import asyncio
import io
import json
from contextvars import ContextVar
from typing import Any

import pytest
from PIL import Image

from anonymization_pipeline import metrics
from anonymization_pipeline.domain import Result
from anonymization_pipeline.extraction import native_pdf
from anonymization_pipeline.pipeline import anonymize
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.pipeline_config.schema import ModelSpec
from anonymization_pipeline.vlm import VLMClient, VLMResponse
from tests.pipeline import FakeVLM, IdealModel, pipeline_config
from tests.unit.test_extraction import LINES, pdf_with_text

VALUES = {
    "Mario Rossi": "PERSONA",
    "RSSMRA80A01H501U": "CODICE_FISCALE",
    "Anna Bianchi": "MEDICO",
}
MARKDOWN = {
    500: "# Referto\nPaziente: Mario Rossi\nCodice fiscale: RSSMRA80A01H501U",
    510: "Firma: [FIRMA] Anna Bianchi",
}
_PHASE: ContextVar[str] = ContextVar("phase")


class MeasuredClient(FakeVLM):
    """Like FakeVLM, but goes through VLMClient.call (semaphore, stats, metrics): ``_send`` answers."""

    async def call(
        self,
        phase: str,
        system: str,
        user: str,
        images: list[bytes] | None = None,
        schema: dict[str, Any] | None = None,
    ) -> VLMResponse:
        self.calls.append((phase, user, len(images or [])))
        _PHASE.set(phase)  # local to the task of the call
        return await VLMClient.call(self, phase, system, user, images, schema)

    async def _send(
        self,
        model: str,
        spec: ModelSpec,
        system: str,
        user: str,
        images: list[bytes],
        schema: dict[str, Any] | None,
        max_tokens: int,
        timeout: float,
    ) -> VLMResponse:
        await asyncio.sleep(0.01)
        answer = self.responder(_PHASE.get(), system, user, images, schema)
        assert isinstance(answer, str)
        return VLMResponse(answer, tokens_in=100, tokens_out=10)


def _tiff() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (500, 300), "white").save(
        buffer,
        "TIFF",
        save_all=True,
        append_images=[Image.new("RGB", (510, 300), "white")],
    )
    return buffer.getvalue()


def _run(
    cfg: PipelineConfig | None = None, name: str = "scan.tif", data: bytes | None = None
) -> Result:
    cfg = cfg or pipeline_config(default_mode="tag")
    client = MeasuredClient(cfg, IdealModel(VALUES, markdown=MARKDOWN))
    return asyncio.run(anonymize(name, data or _tiff(), cfg, client))


def test_metrics_per_phase_and_per_page() -> None:
    result = _run()
    m = result.metrics
    assert m is not None

    assert set(m) == {
        "version", "document", "configuration", "times_s", "throughput", "extraction",
        "detection", "redaction", "verification", "vlm", "pages", "resources",
    }  # fmt: skip
    assert m["times_s"] == result.report["times_s"] and m["document"]["pages"] == 2
    for phase in ("extraction", "detection", "verification"):
        assert m["vlm"][phase]["calls"] == result.report["vlm"][phase]["calls"]
        assert m["vlm"][phase]["latency_s"]["max"] >= 0.01
        assert m["vlm"][phase]["tokens_out_per_s"] > 0
    passes = len(pipeline_config().passes("detection"))
    assert [p["page"] for p in m["pages"]] == [1, 2]
    for page in m["pages"]:
        assert page["extracted_with"] == "vlm" and page["extraction_s"] >= 0.01
        assert page["detection_s"]["vlm"] > 0 and page["verification_s"] > 0
        assert (
            page["vlm_calls"] == 1 + 2 * passes
        )  # extraction, detection, verification (one round)
    # model time per page, without the queue wait: the sum over the pages is the call duration
    detection_total = sum(p["detection_s"]["vlm"] for p in m["pages"])
    assert abs(detection_total - m["vlm"]["detection"]["duration_s"]) < 0.01
    assert (
        abs(
            sum(p["verification_s"] for p in m["pages"])
            - m["vlm"]["verification"]["duration_s"]
        )
        < 0.01
    )
    assert (
        m["extraction"]["vlm"]["pages"] == 2
        and m["extraction"]["docling"]["pages"] == 0
    )
    assert "iterations" not in m["redaction"] and "iterations" not in m["verification"]
    assert (
        m["detection"]["rules"]["values"]
        == result.report["detection"]["sources"]["rules"]
    )
    assert m["throughput"]["s_per_page"] > 0 and m["resources"]["cpus_available"]


def test_metrics_hold_no_values_and_no_file_name() -> None:
    text = json.dumps(_run(name="Rossi_Mario.tif").metrics, ensure_ascii=False)

    for value in [*VALUES, "Rossi_Mario", "Firma"]:
        assert value not in text


def test_hybrid_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        native_pdf,
        "convert",
        lambda data, pages, c: {i: "Paziente: Mario Rossi" for i in pages},
    )

    result = _run(name="r.pdf", data=pdf_with_text([(line, "") for line in LINES]))

    m = result.metrics
    assert m is not None
    assert (
        m["pages"][0]["extracted_with"] == "docling"
        and m["pages"][0]["extraction_s"] is None
    )
    assert m["extraction"]["docling"] == {
        "pages": 1,
        "duration_s": m["extraction"]["docling"]["duration_s"],
    }
    assert "extraction" not in m["vlm"]


def test_metrics_disabled() -> None:
    result = _run(
        pipeline_config({"output": {"save_metrics": False}}, default_mode="tag")
    )

    assert result.metrics is None and "metrics" not in result.output_names()


def test_distribution() -> None:
    assert metrics.distribution([]) is None
    assert metrics.distribution([2.0]) == {
        "mean": 2.0,
        "p50": 2.0,
        "p95": 2.0,
        "max": 2.0,
    }
    d = metrics.distribution([float(x) for x in range(1, 101)])
    assert d == {"mean": 50.5, "p50": 50.5, "p95": 95.05, "max": 100.0}


def test_without_a_collector_nothing_is_recorded() -> None:
    with metrics.measure("x"):
        pass
    metrics.record_call(metrics.Call("detection", None, 1.0, 0.0, 0))

    assert metrics.current() is None
