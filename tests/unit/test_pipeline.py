import asyncio
import io
import json
import os
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
    restore_pdf,
)
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.redaction import structure
from anonymization_pipeline.redaction.structure import (
    ATTACHMENT,
    StructureError,
    StructureLeak,
)
from anonymization_pipeline.redaction.vault import VaultError, generate_key
from anonymization_pipeline.rendering import attachment, text_of
from anonymization_pipeline.verification import UnredactableContent
from anonymization_pipeline.vlm import VLMError
from tests.pipeline import (
    FIXTURES,
    FakeVLM,
    IdealModel,
    fake_convert,
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
    result, client = _run(IdealModel(VALUES))

    for value in VALUES:
        assert value not in result.markdown
    assert (
        "Paziente: [PERSONA_1]" in result.markdown
        and "sig. [PERSONA_2] rientra" in result.markdown
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


def test_patient_with_an_eponym_surname_is_redacted(log_stream: io.StringIO) -> None:
    """Parkinson the patient is redacted everywhere, the morbo di Parkinson is not a person."""
    text = (
        "Paziente: Parkinson Mario\nAffetto da morbo di Parkinson.\n"
        "Il sig. Parkinson rientra a domicilio.\n"
    )
    model = IdealModel(
        {"Parkinson Mario": "PERSONA", "Parkinson": "COGNOME"},
    )
    cfg = pipeline_config(
        {"categories": {"groups": {"sanitari": False}}}, default_mode="tag"
    )

    result, _ = _run(model, cfg, data=text.encode(), key=None)

    assert result.markdown == (
        "Paziente: [PERSONA_1]\nAffetto da morbo di Parkinson.\n"
        "Il sig. [PERSONA_2] rientra a domicilio.\n"
    )
    assert result.report["verification"]["outcome"] == "ok"
    assert "Parkinson" not in log_stream.getvalue()


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
    result, client = _run(IdealModel(VALUES))

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
    start = result.markdown.index("RSSMRA80A01H501U")
    assert result.report["verification"]["residuals"] == [
        {"category": "CODICE_FISCALE", "page": 1, "start": start, "end": start + 16}
    ]


def test_residual_offsets_point_into_the_whole_markdown(
    log_stream: io.StringIO,
) -> None:
    """Offsets of a residual on page 2 count the page separator; the report never has the value."""
    buffer = io.BytesIO()
    Image.new("RGB", (500, 300), "white").save(
        buffer,
        "TIFF",
        save_all=True,
        append_images=[Image.new("RGB", (510, 300), "white")],
    )
    markdown = {
        500: "# Pagina uno\nPaziente: Mario Rossi",
        510: "Firma: Anna Bianchi",
    }
    model = IdealModel(VALUES, markdown=markdown, missed=("Anna Bianchi",))
    cfg = pipeline_config({"phases": VLM_ONLY}, default_verification="report")

    result, _ = _run(model, cfg, name="scan.tif", data=buffer.getvalue())

    residuals = result.report["verification"]["residuals"]
    assert [(r["category"], r["page"]) for r in residuals] == [("MEDICO", 2)]
    assert (
        result.markdown[residuals[0]["start"] : residuals[0]["end"]] == "Anna Bianchi"
    )
    assert "Anna Bianchi" not in json.dumps(result.report, ensure_ascii=False)
    assert "Anna Bianchi" not in log_stream.getvalue()


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


def test_invariant_sees_a_value_left_across_table_cells() -> None:
    piece = Occurrence(
        2, 15, "[INTERVENTO_1]", "Derivazione", "INTERVENTO", "Derivazione esterna"
    )

    with pytest.raises(UnredactableContent, match="invariant"):
        check_invariant(
            ["| [INTERVENTO_1] | esterna |", "| Derivazione | esterna |"],
            [[piece], []],
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
    monkeypatch.setattr(native_pdf, "convert", fake_convert(lambda i: markdown))
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
        "verification": "checker:1",
    }
    contexts = {
        "reader:1": 16384,
        "finder:1": 8192,
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


def test_detection_sends_the_image_only_to_models_with_vision(tmp_path: Path) -> None:
    def run(text_only: tuple[str, ...]) -> FakeVLM:
        directory = tmp_path / (
            "blind" if text_only else "seer"
        )  # the file is read once
        directory.mkdir()
        cfg = pipeline_config(
            models_file=models_file(
                directory,
                {"seer:1": 8192, "reader:1": 8192},
                {"detection": "reader:1"},
                text_only,
            )
        )
        model = IdealModel(VALUES, markdown={400: TEXT})
        _, client = _run(model, cfg, "a.png", png(400))
        return client

    seen = run(())
    assert all(n == 1 for phase, _, n in seen.calls if phase == "detection")
    blind = run(("reader:1",))
    assert [n for phase, _, n in blind.calls if phase == "detection"]
    assert all(n == 0 for phase, _, n in blind.calls if phase == "detection")
    assert all(n == 1 for phase, _, n in blind.calls if phase == "extraction")


def test_common_word_left_in_lowercase_does_not_break_the_invariant() -> None:
    # "ROSSI" is the patient, "globuli rossi" is not: redaction leaves the common word and the
    # final check must not take it for the surname left in clear text
    text = (
        "Paziente: ROSSI MARIO\nCampione: Rossi M.\nEritrociti (globuli rossi) 5,11\n"
    )
    model = IdealModel({"ROSSI MARIO": "PERSONA", "Rossi": "COGNOME"})
    cfg = pipeline_config(
        {"phases": VLM_ONLY}, default_verification="report", default_mode="tag"
    )

    result, _ = _run(model, cfg, data=text.encode(), key=None)

    assert "globuli rossi" in result.markdown
    assert "ROSSI" not in result.markdown and "Rossi M." not in result.markdown


# --- layout PDF ---------------------------------------------------------------------------------

LAYOUT = {"output": {"layout_pdf": True}}


def _clear_text(pdf: bytes) -> str:
    return " ".join(" ".join(text_of(pdf)).split())


def test_layout_pdf_is_off_by_default() -> None:
    result, _ = _run(IdealModel(VALUES))

    assert result.layout_pdf is None and result.structure is None
    assert result.report["layout"] == "off"
    assert result.encrypted_map is not None
    with pytest.raises(StructureError, match="non contiene"):
        restore_pdf(b'{"name": "x"}', result.encrypted_map, KEY)


def test_layout_pdf_of_a_text_document_and_its_restore() -> None:
    result, _ = _run(IdealModel(VALUES), pipeline_config(LAYOUT))

    assert result.report["layout"] == "pdf"
    assert result.layout_pdf is not None and result.structure is not None
    censored = _clear_text(result.layout_pdf)
    assert "[PERSONA_1]" in censored and "[CODICE_FISCALE_1]" in censored
    for value in VALUES:
        assert value not in censored
        assert value not in result.structure.decode()
    assert attachment(result.layout_pdf, ATTACHMENT) == result.structure
    assert result.encrypted_map is not None

    for document in (result.layout_pdf, result.structure):
        original = _clear_text(restore_pdf(document, result.encrypted_map, KEY))
        assert "Paziente: Mario Rossi" in original and "RSSMRA80A01H501U" in original
    with pytest.raises(VaultError):
        restore_pdf(result.layout_pdf, result.encrypted_map, generate_key())


def test_layout_pdf_of_native_pages_keeps_the_docling_positions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown = "Paziente: Mario Rossi\nDiagnosi: diabete mellito tipo 2"
    monkeypatch.setattr(native_pdf, "convert", fake_convert(lambda i: markdown))
    data = pdf_with_text([(line, "") for line in LINES])

    result, _ = _run(IdealModel(VALUES), pipeline_config(LAYOUT), "a.pdf", data)

    assert result.structure is not None and result.layout_pdf is not None
    structure = json.loads(result.structure)
    assert [t["text"] for t in structure["texts"]] == [
        "Paziente: [PERSONA_1]",
        "Diagnosi: [DIAGNOSI_1]",
    ]
    assert structure["texts"][0]["prov"][0]["bbox"]["t"] == 800
    assert result.encrypted_map is not None
    original = _clear_text(restore_pdf(result.layout_pdf, result.encrypted_map, KEY))
    assert "Paziente: Mario Rossi" in original


def test_layout_pdf_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def leak(*args: object) -> None:
        raise StructureLeak("test")

    monkeypatch.setattr(structure, "check", leak)

    result, _ = _run(IdealModel(VALUES), pipeline_config(LAYOUT))

    assert result.layout_pdf is None and result.structure is None
    assert result.report["layout"] == "unavailable"
    assert "layout_unavailable" in result.report["warnings"]
    assert result.markdown  # the Markdown is still returned


def test_layout_pdf_in_redaction_mode_has_no_map() -> None:
    result, _ = _run(
        IdealModel(VALUES),
        pipeline_config(LAYOUT, default_mode="redaction"),
        key=None,
    )

    assert result.layout_pdf is not None and result.encrypted_map is None
    assert "█████" in _clear_text(result.layout_pdf)


@pytest.mark.docling
def test_layout_pdf_with_real_docling() -> None:
    """Image with the 'docling' extra and the models in ANONPIP_DOCLING_MODELS_DIR (or the default)."""
    folder = os.environ.get("ANONPIP_DOCLING_MODELS_DIR", "/models/docling")
    cfg = pipeline_config(LAYOUT, docling_models_dir=folder)
    c = cfg.phases.extraction.native_pdf
    if not native_pdf.DOCLING_INSTALLED or not native_pdf.models_present(c):
        pytest.skip("docling or its models are missing")
    values = {
        "ESPOSITO FRANCESCA": "PERSONA",
        "SPSFNC72M67G273V": "CODICE_FISCALE",
        "Salvatore Lo Bue": "FAMILIARE",
    }
    data = (FIXTURES / "referto_pdf_nativo.pdf").read_bytes()

    result, _ = _run(IdealModel(values), cfg, "referto.pdf", data)

    assert result.report["layout"] == "pdf", result.report["warnings"]
    assert result.report["extraction"]["docling"] == [1]
    assert result.layout_pdf is not None and result.structure is not None
    structure = json.loads(result.structure)
    assert structure["texts"] and all(
        t["prov"][0]["page_no"] == 1 for t in structure["texts"]
    )
    censored = _clear_text(result.layout_pdf)
    assert "VISITA CARDIOLOGICA" in censored
    for value in values:
        assert value not in censored and value not in result.structure.decode()
    assert result.encrypted_map is not None
    original = _clear_text(restore_pdf(result.layout_pdf, result.encrypted_map, KEY))
    for value in values:
        assert value in original
