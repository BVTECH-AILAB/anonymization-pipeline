"""The engine as a library: conversion, releases, edits, errors, configuration."""

from pathlib import Path

import pytest

from anonmyzation_pipeline import errors as service_errors
from anonmyzation_pipeline.services.anonymization import errors
from anonmyzation_pipeline.services.anonymization.config import PipelineConfig
from anonmyzation_pipeline.services.anonymization.convert import upload_to_pdf
from anonmyzation_pipeline.services.anonymization.document import (
    apply_edits,
    display_boxes,
    release_markdown,
    release_pdf,
    render_png,
)
from anonmyzation_pipeline.services.anonymization.factory import build_pipeline
from anonmyzation_pipeline.services.anonymization.pipeline import Analysis, Pipeline
from tests.conftest import FAKE_CF, make_settings
from tests.documents import FakeNER, FakeReader, draw_pdf, edit_pdf, letter_pdf


@pytest.fixture
def analyzed(tmp_path: Path) -> tuple[Path, Analysis]:
    src = letter_pdf(tmp_path / "letter.pdf")
    return src, Pipeline(FakeReader(), FakeNER()).analyze(src, visual=False)


def test_upload_to_pdf_raises_engine_errors(tmp_path: Path) -> None:
    locked = edit_pdf(
        draw_pdf(tmp_path / "locked.pdf", lambda p: p.text(72, 72, "x")), password="pw"
    )
    many = draw_pdf(tmp_path / "many.pdf", lambda p: None, lambda p: None)

    with pytest.raises(errors.UnsupportedFormatError):
        upload_to_pdf(b"x", ".txt", tmp_path / "a.pdf", 10)
    with pytest.raises(errors.UnreadableDocumentError):
        upload_to_pdf(b"not a pdf", ".pdf", tmp_path / "b.pdf", 10)
    with pytest.raises(errors.PasswordProtectedError):
        upload_to_pdf(locked.read_bytes(), ".pdf", tmp_path / "c.pdf", 10)
    with pytest.raises(errors.TooManyPagesError):
        upload_to_pdf(many.read_bytes(), ".pdf", tmp_path / "d.pdf", 1)


def test_engine_errors_are_independent_of_the_service() -> None:
    for cls in (
        errors.NerUnavailableError,
        errors.UnsupportedFormatError,
        errors.UnreadableDocumentError,
        errors.PasswordProtectedError,
        errors.TooManyPagesError,
        errors.PageNotFoundError,
    ):
        assert issubclass(cls, errors.AnonymizationError)
        assert not issubclass(cls, service_errors.ServiceError)


@pytest.mark.parametrize(
    ("engine_error", "code", "status"),
    [
        (errors.NerUnavailableError, "NER_UNAVAILABLE", 503),
        (errors.UnsupportedFormatError, "UNSUPPORTED_FORMAT", 415),
        (errors.UnreadableDocumentError, "UNREADABLE_DOCUMENT", 422),
        (errors.PasswordProtectedError, "PASSWORD_PROTECTED", 422),
        (errors.TooManyPagesError, "TOO_MANY_PAGES", 413),
        (errors.PageNotFoundError, "JOB_NOT_FOUND", 404),
        (errors.AnonymizationError, "INTERNAL_ERROR", 500),
    ],
)
def test_service_error_maps_every_engine_error(
    engine_error: type[errors.AnonymizationError], code: str, status: int
) -> None:
    mapped = service_errors.service_error(engine_error())

    assert (mapped.code, mapped.status_code) == (code, status)


def test_release_pdf_writes_both_verified_files(
    analyzed: tuple[Path, Analysis], tmp_path: Path
) -> None:
    src, analysis = analyzed
    steps: list[str] = []

    release = release_pdf(
        src,
        analysis,
        tmp_path / "anonymized.pdf",
        tmp_path / "anonymized.docx",
        style="black",
        tol=2e-4,
        progress=lambda step, _: steps.append(step),
    )

    assert release.verified is True
    assert release.issues == []
    assert set(release.seconds) == {"redaction", "verification"}
    assert steps == ["redact", "preview"]
    assert (tmp_path / "anonymized.pdf").exists()
    assert (tmp_path / "anonymized.docx").exists()
    assert not list(tmp_path.glob("*.next.*"))


def test_a_failed_release_keeps_the_previous_version(
    analyzed: tuple[Path, Analysis], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, analysis = analyzed
    pdf, docx = tmp_path / "anonymized.pdf", tmp_path / "anonymized.docx"
    release_pdf(src, analysis, pdf, docx, style="black", tol=2e-4)
    before = pdf.read_bytes(), docx.read_bytes()

    def broken(*args, **kwargs):
        raise RuntimeError("export failed")

    monkeypatch.setattr(
        "anonmyzation_pipeline.services.anonymization.document.export_docx", broken
    )
    with pytest.raises(RuntimeError):
        release_pdf(src, analysis, pdf, docx, style="black", tol=2e-4)

    assert (pdf.read_bytes(), docx.read_bytes()) == before
    assert not list(tmp_path.glob("*.next.*"))


def test_release_markdown(analyzed: tuple[Path, Analysis], tmp_path: Path) -> None:
    _, analysis = analyzed
    dest = tmp_path / "anonymized.md"

    release = release_markdown(analysis, dest)

    assert release.verified is True
    assert FAKE_CF not in dest.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.next.*"))


def test_apply_edits(analyzed: tuple[Path, Analysis]) -> None:
    _, analysis = analyzed
    n = len(analysis.boxes)

    outcome = apply_edits(
        analysis, add=[(0, (10, 10, 50, 30)), (9, (0, 0, 1, 1))], remove=[0, 999]
    )

    assert outcome.added == 1
    assert analysis.boxes[0].redact is False
    assert "removed-by-user" in analysis.boxes[0].sources
    assert len(analysis.boxes) == n + 1
    assert analysis.boxes[-1].layer == "manual"

    apply_edits(analysis, restore=[0])

    assert analysis.boxes[0].redact is True


def test_display_boxes_carry_their_id(analyzed: tuple[Path, Analysis]) -> None:
    _, analysis = analyzed

    boxes = display_boxes(analysis)

    assert [b["id"] for b in boxes] == list(range(len(analysis.boxes)))


def test_render_png(analyzed: tuple[Path, Analysis], tmp_path: Path) -> None:
    src, _ = analyzed

    render_png(src, 0, 50, tmp_path / "p.png")

    assert (tmp_path / "p.png").read_bytes().startswith(b"\x89PNG")
    with pytest.raises(errors.PageNotFoundError):
        render_png(src, 5, 50, tmp_path / "q.png")


def test_settings_build_the_engine_config() -> None:
    config = make_settings(
        ocr_server_url="http://ocr.test",
        ner_url="http://ner.test",
        ner_retries=4,
        redact_age=True,
    ).pipeline_config()

    assert isinstance(config, PipelineConfig)
    assert config.ocr_server_url == "http://ocr.test"
    assert config.ner_url == "http://ner.test"
    assert config.ner_retries == 4
    assert config.redaction_policy["AGE"] is True


def test_build_pipeline_from_a_plain_config() -> None:
    pipeline = build_pipeline(
        PipelineConfig(ner_enabled=False, redaction_policy={"AGE": True})
    )

    assert pipeline.ner is None
    assert pipeline.policy["AGE"] is True
