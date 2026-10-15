import io
from pathlib import Path

import pytest
from PIL import Image

from anonymization_pipeline.ingest import DocumentError
from anonymization_pipeline.pipeline_config import PipelineConfig, load_pipeline_config
from anonymization_pipeline.render.pdf import Redacted
from anonymization_pipeline.services import anonymization as service
from anonymization_pipeline.services.anonymization import PHASES, anonymize
from anonymization_pipeline.verify.pdf import VerificationReport
from tests.conftest import DEFAULTS_FILE, FAKE_CF, make_settings
from tests.documents import PATIENT, letter_pdf, trapped_letter


@pytest.fixture
def cfg() -> PipelineConfig:
    return load_pipeline_config(make_settings(pipeline_config_file=DEFAULTS_FILE))


def test_letter_is_anonymized_and_verified(tmp_path: Path, cfg: PipelineConfig) -> None:
    phases: list[str] = []

    result = anonymize(
        trapped_letter(tmp_path / "in.pdf").read_bytes(), cfg, phases.append
    )

    assert (result.status, result.reason, result.pages, result.kind) == (
        "done",
        None,
        1,
        "pdf",
    )
    assert result.pdf is not None
    assert FAKE_CF.encode() not in result.pdf
    assert result.counts["CODICE_FISCALE"] == 1
    assert result.boxes and result.strategies == {0: "surgical"}
    assert tuple(phases) == PHASES
    assert set(result.timings) == {*PHASES, "total"}


def test_result_never_holds_values(tmp_path: Path, cfg: PipelineConfig) -> None:
    result = anonymize(letter_pdf(tmp_path / "in.pdf").read_bytes(), cfg)

    text = repr(result)
    for value in (FAKE_CF, PATIENT, "Laura Bianchi"):
        assert value not in text


def test_scanned_pages_are_blocked_without_ocr(cfg: PipelineConfig) -> None:
    image = Image.new("RGB", (800, 1100), "white")
    buf = io.BytesIO()
    image.save(buf, "PNG")

    result = anonymize(buf.getvalue(), cfg)

    assert (result.status, result.reason) == ("blocked", "ocr_unavailable")
    assert result.scanned_pages == [0] and result.pdf is None


def test_failed_verification_blocks_the_document(
    tmp_path: Path, cfg: PipelineConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(
        original: bytes, redacted: Redacted, values: object, c: object, ner: object
    ) -> VerificationReport:
        report = VerificationReport()
        report.fail("value_in_text", 0, "CODICE_FISCALE")
        return report

    monkeypatch.setattr(service, "verify", failing)

    result = anonymize(letter_pdf(tmp_path / "in.pdf").read_bytes(), cfg)

    assert (result.status, result.reason, result.pdf) == (
        "blocked",
        "verification_failed",
        None,
    )
    assert [f.check for f in result.failures] == ["value_in_text"]


def test_invalid_document_is_an_error_of_the_caller(cfg: PipelineConfig) -> None:
    with pytest.raises(DocumentError):
        anonymize(b"not a document", cfg)


def test_progress_can_cancel_the_run(tmp_path: Path, cfg: PipelineConfig) -> None:
    class Cancelled(Exception):
        pass

    def progress(phase: str) -> None:
        if phase == "redaction":
            raise Cancelled

    with pytest.raises(Cancelled):
        anonymize(letter_pdf(tmp_path / "in.pdf").read_bytes(), cfg, progress)
