import asyncio
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from anonymization_pipeline.domain import Occurrence, Result
from anonymization_pipeline.extraction import native_pdf
from anonymization_pipeline.pipeline import (
    MissingKeyError,
    Progress,
    anonymize,
    check_invariant,
    restore,
)
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.redaction.vault import generate_key
from anonymization_pipeline.verification import UnredactableContent
from anonymization_pipeline.vlm import VLMError
from tests.pipeline import (
    FakeVLM,
    IdealModel,
    models_file,
    pipeline_config,
    png,
)
from tests.unit.test_extraction import LINES, pdf_with_text

TEXT = (
    "# Referto\n\nPaziente: Mario Rossi\nCodice fiscale: RSSMRA80A01H501U\n"
    "Diagnosi: diabete mellito tipo 2\nMedico: Dott.ssa Anna Bianchi\n\nIl sig. Rossi Mario rientra a domicilio.\n"
)
VALUES = {
    "Mario Rossi": "PERSONA",
    "Rossi Mario": "PERSONA",
    "RSSMRA80A01H501U": "CODICE_FISCALE",
    "diabete mellito tipo 2": "DIAGNOSI",
    "Anna Bianchi": "MEDICO",
}
KEY = generate_key()
# only the VLM detector: for the tests in which a value escapes detection and verification must
# find it (rules and labels would find the tax code at once)
VLM_ONLY = {
    "detection": {
        "detectors": {"rules": False, "labels": False, "ner": {"enabled": False}}
    }
}


def _run(
    model: IdealModel,
    cfg: PipelineConfig | None = None,
    name: str = "referto.txt",
    data: bytes = TEXT.encode(),
    key: str | None = KEY,
    progress: Progress | None = None,
) -> tuple[Result, FakeVLM]:
    cfg = cfg or pipeline_config()
    client = FakeVLM(cfg, model)
    return asyncio.run(anonymize(name, data, cfg, client, key, progress)), client


def test_text_end_to_end_and_restore() -> None:
    result, client = _run(IdealModel(VALUES, groups=[["Mario Rossi", "Rossi Mario"]]))

    for value in VALUES:
        assert value not in result.markdown
    assert (
        "Paziente: [PERSONA_1]" in result.markdown
        and "sig. [PERSONA_1] rientra" in result.markdown
    )
    assert "[MEDICO_1]" in result.markdown and "[CODICE_FISCALE_1]" in result.markdown
    assert "[DIAGNOSI_1]" in result.markdown
    assert result.markdown.startswith("# Referto\n\n")  # Markdown structure kept
    report = json.dumps(result.report, ensure_ascii=False)
    for value in VALUES:  # report without values
        assert value not in report
    assert result.report["verification"] == {
        "mode": "block",  # the default
        "outcome": "ok",
        "residuals": [],
    }
    assert result.report["counts"] == {
        "CODICE_FISCALE": 1,
        "DIAGNOSI": 1,
        "MEDICO": 1,
        "PERSONA": 2,
    }
    assert "extraction" not in client.phases()  # text file: no extraction
    assert result.encrypted_map is not None
    assert restore(result.markdown, result.encrypted_map, KEY) == TEXT


def test_multipage_images_extracted_by_the_vlm() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (500, 300), "white").save(
        buffer,
        "TIFF",
        save_all=True,
        append_images=[Image.new("RGB", (510, 300), "white")],
    )
    markdown = {
        500: "# Pagina uno\nPaziente: Mario Rossi",
        510: "Firma: [FIRMA] Anna Bianchi",
    }

    result, client = _run(
        IdealModel(VALUES, markdown=markdown), name="scan.tif", data=buffer.getvalue()
    )

    assert (
        result.markdown
        == "# Pagina uno\nPaziente: [PERSONA_1]\n\n<!-- pagina 2 -->\n\nFirma: [FIRMA] [MEDICO_1]"
    )
    # extraction and detection see the page image; verification reads the clear Markdown instead
    assert all(
        n == (0 if phase == "verification" else 1)
        for phase, _, n in client.calls
        if phase in ("extraction", "detection", "verification")
    )
    original = "# Pagina uno\nPaziente: Mario Rossi\n\n<!-- pagina 2 -->\n\nFirma: [FIRMA] Anna Bianchi"
    assert result.encrypted_map is not None
    assert restore(result.markdown, result.encrypted_map, KEY) == original


def test_verification_compares_with_the_clear_markdown() -> None:
    result, client = _run(IdealModel(VALUES, groups=[["Mario Rossi", "Rossi Mario"]]))

    assert result.report["verification"]["outcome"] == "ok"
    prompts = [user for phase, user, _ in client.calls if phase == "verification"]
    assert prompts
    for user in prompts:
        # the redacted page to check and, as reference, the clear original of the same page
        assert "Paziente: [PERSONA_1]" in user and "Paziente: Mario Rossi" in user
        # original first: with the opposite order the model stops seeing a value left in clear
        assert user.index("Paziente: Mario Rossi") < user.index("Paziente: [PERSONA_1]")
    assert all(n == 0 for phase, _, n in client.calls if phase == "verification")


def test_without_the_image_when_disabled() -> None:
    cfg = pipeline_config({"phases": {"detection": {"use_image": False}}})

    _, client = _run(
        IdealModel(VALUES, markdown={400: "Mario Rossi"}), cfg, "a.png", png(400)
    )

    assert all(n == 0 for phase, _, n in client.calls if phase != "extraction")


def test_hallucinations_are_discarded() -> None:
    result, _ = _run(IdealModel(VALUES, invented=("Giuseppe Verdi",)))

    assert result.report["detection"]["hallucinations_discarded"] >= 1
    assert "Giuseppe Verdi" not in result.markdown


def test_verification_blocks_by_default_and_can_report() -> None:
    model = IdealModel(VALUES, missed=("RSSMRA80A01H501U",))

    with pytest.raises(UnredactableContent) as error:
        _run(model, pipeline_config({"phases": VLM_ONLY}))  # block is the default
    assert error.value.as_dict() == {
        "reason": "residuals",
        "residuals": [{"category": "CODICE_FISCALE", "page": 1}],
    }
    assert "RSSMRA" not in str(error.value)  # never values in errors

    result, _ = _run(
        model, pipeline_config({"phases": VLM_ONLY}, default_verification="report")
    )
    assert (
        "RSSMRA80A01H501U" in result.markdown
        and "residuals_not_fixed" in result.report["warnings"]
    )
    assert result.report["verification"]["residuals"] == [
        {"category": "CODICE_FISCALE", "page": 1}
    ]


def test_fail_closed_when_verification_does_not_answer() -> None:
    model = IdealModel(VALUES, errors={"verification": VLMError("timeout")})

    with pytest.raises(UnredactableContent, match="verification_unavailable"):
        _run(model)
    result, _ = _run(
        model, pipeline_config({"phases": {"verification": {"required": False}}})
    )
    assert result.report["verification"]["outcome"] == "skipped"
    assert "verification_skipped" in result.report["warnings"]


def test_detection_error_propagates() -> None:
    with pytest.raises(VLMError):
        _run(IdealModel(VALUES, errors={"detection": VLMError("down")}))


def test_tag_and_redaction_modes_and_key() -> None:
    result, _ = _run(IdealModel(VALUES), pipeline_config(default_mode="tag"), key=None)
    assert result.encrypted_map is None and "[PERSONA_1]" in result.markdown

    result, _ = _run(
        IdealModel(VALUES), pipeline_config(default_mode="redaction"), key=None
    )
    assert "█████" in result.markdown and "[" not in result.markdown.replace(
        "# Referto", ""
    )

    with pytest.raises(MissingKeyError, match="chiave"):
        _run(IdealModel(VALUES), key=None)


def test_verification_off_and_transcript() -> None:
    cfg = pipeline_config(
        {"output": {"save_transcript": True}}, default_verification="off"
    )

    result, client = _run(IdealModel(VALUES), cfg)

    assert (
        "verification" not in client.phases()
        and result.report["verification"]["outcome"] == "off"
    )
    assert result.transcript == TEXT and "transcript" in result.output_names()


def test_invariant() -> None:
    occurrence = Occurrence(0, 11, "[PERSONA_1]", "Mario Rossi", "PERSONA")

    check_invariant(["[PERSONA_1] ok"], [[occurrence]], pipeline_config())
    with pytest.raises(UnredactableContent, match="invariant"):
        check_invariant(
            ["[PERSONA_1]", "di nuovo Mario Rossi"],
            [[occurrence], []],
            pipeline_config(),
        )


def test_progress_and_tag_positions() -> None:
    phases: list[tuple[str, dict]] = []
    cfg = pipeline_config({"output": {"save_transcript": True}}, default_mode="tag")

    result, _ = _run(
        IdealModel(VALUES), cfg, key=None, progress=lambda p, d: phases.append((p, d))
    )

    assert [p for p, _ in phases] == [
        "ingest",
        "extraction",
        "detection",
        "redaction",
        "verification",
        "completed",
    ]
    assert phases[0][1]["metrics"] is not None and phases[4][1] == {}
    # tags: positions in the redacted text, without values
    assert result.tags and all(
        result.markdown[t["start"] : t["end"]] == t["replacement"] for t in result.tags
    )
    assert not any(value in json.dumps(result.tags) for value in VALUES)
    # with the transcript: positions in the clear text too
    assert result.transcript == TEXT and result.occurrences
    for o in result.occurrences:
        assert (
            result.transcript[o["original_start"] : o["original_end"]] == o["original"]
        )
        assert (
            result.markdown[o["start"] : o["end"]] == o["replacement"] and o["category"]
        )


def test_without_transcript_no_clear_text_occurrence() -> None:
    result, _ = _run(IdealModel(VALUES))

    assert result.transcript is None and result.occurrences is None and result.tags


def test_native_pdf_extraction_in_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    markdown = "# Referto\n\nPaziente: Mario Rossi"
    monkeypatch.setattr(
        native_pdf, "convert", lambda data, pages, c: {i: markdown for i in pages}
    )
    data = pdf_with_text([(line, "") for line in LINES])

    result, _ = _run(
        IdealModel({"Mario Rossi": "PERSONA"}),
        pipeline_config(default_mode="tag"),
        "r.pdf",
        data,
        None,
    )

    assert result.report["extraction"] == {"docling": [1], "vlm": []}
    assert result.markdown == "# Referto\n\nPaziente: [PERSONA_1]"


def test_report_of_the_detectors() -> None:
    model = IdealModel(
        {"Mario Rossi": "PERSONA", "RSSMRA80A01H501U": "CODICE_FISCALE"},
        missed=("Mario Rossi", "RSSMRA80A01H501U"),
    )
    cfg = pipeline_config(
        {"phases": {"detection": {"detectors": {"ner": {"enabled": False}}}}},
        default_mode="tag",
    )

    result, _ = _run(
        model, cfg, data=b"Paziente: Mario Rossi\nCF: RSSMRA80A01H501U\n", key=None
    )

    assert result.markdown.startswith("Paziente: [PERSONA_1]\nCF: [CODICE_FISCALE_1]")
    sources = {e["replacement"]: e["sources"] for e in result.report["entities"]}
    assert sources == {"[PERSONA_1]": ["labels"], "[CODICE_FISCALE_1]": ["rules"]}


def test_each_phase_runs_its_own_model(tmp_path: Path) -> None:
    phases = {
        "extraction": "reader:1",
        "detection": "finder:1",
        "redaction": "grouper:1",
        "verification": "checker:1",
    }
    contexts = {
        "reader:1": 16384,
        "finder:1": 8192,
        "grouper:1": 4096,
        "checker:1": 8192,
    }
    cfg = pipeline_config(
        {"output": {"save_metrics": True}},
        models_file=models_file(tmp_path, contexts, phases),
    )
    buffer = io.BytesIO()
    Image.new("RGB", (500, 300), "white").save(buffer, "PNG")
    model = IdealModel(VALUES, markdown={500: TEXT})

    result, client = _run(model, cfg, name="scan.png", data=buffer.getvalue())

    assert {phase for phase, _ in client.used_models} == set(phases)
    assert all(name == phases[phase] for phase, name in client.used_models)
    assert result.report["model"] == {"backend": "ollama", "phases": phases}
    assert result.metrics is not None
    assert result.metrics["configuration"]["models"] == {
        phase: {"name": name, "context": contexts[name]}
        for phase, name in phases.items()
    }
