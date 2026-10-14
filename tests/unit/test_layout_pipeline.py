import asyncio
import io
import json

import pypdfium2 as pdfium
import pytest
from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    ProvenanceItem,
)

from anonymization_pipeline.domain import Result
from anonymization_pipeline.extraction import native_pdf
from anonymization_pipeline.extraction.native_pdf import Conversion
from anonymization_pipeline.layout import model as layout_model
from anonymization_pipeline.layout import pdf as layout_pdf
from anonymization_pipeline.layout.pdf import extract
from anonymization_pipeline.pipeline import anonymize, restore_pdf
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.pipeline_config.schema import NativePdf
from anonymization_pipeline.redaction import structure, vault
from anonymization_pipeline.redaction.structure import StructureError
from anonymization_pipeline.redaction.vault import generate_key
from anonymization_pipeline.rendering import attach, attachment, render_pdf, text_of
from tests.layout import CF as BOXED_CF
from tests.layout import X, boxed_pdf, boxed_structure, convert_lines, styled_pdf
from tests.pipeline import FakeVLM, IdealModel, pipeline_config

CF = "RSSMRA85M01H501U"
VALUES = {
    "Mario Rossi": "PERSONA",
    CF: "CODICE_FISCALE",
    "diabete mellito tipo 2": "DIAGNOSI",
}
KEY = generate_key()
LAYOUT = {"output": {"layout_pdf": True}}


def _run(
    monkeypatch: pytest.MonkeyPatch, overrides: dict | None = None
) -> tuple[Result, PipelineConfig]:
    monkeypatch.setattr(native_pdf, "convert", convert_lines())
    cfg = pipeline_config({**LAYOUT, **(overrides or {})})
    client = FakeVLM(cfg, IdealModel(VALUES))
    result = asyncio.run(anonymize("referto.pdf", styled_pdf(), cfg, client, KEY))
    return result, cfg


def _words(pdf: bytes) -> dict[int, list[tuple[float, str]]]:
    """Baseline -> (left edge, word) of the first page of ``pdf``."""
    lines: dict[int, list[tuple[float, str, float]]] = {}
    for g in extract(pdf, {1}, graphics=False).pages[1].glyphs:
        line = lines.setdefault(round(g.baseline), [])
        if line and g.x0 <= line[-1][2] + 1:
            x0, word, _ = line[-1]
            line[-1] = (x0, word + g.char, g.x1)
        else:
            line.append((g.x0, g.char, g.x1))
    return {y: [(round(x, 1), w) for x, w, _ in line] for y, line in lines.items()}


def test_native_pages_keep_positions_styles_and_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _ = _run(monkeypatch)

    assert result.report["layout"] == "pdf"
    assert "layout_style_unavailable" not in result.report["warnings"]
    assert result.layout_pdf is not None and result.layout is not None
    assert attachment(result.layout_pdf, layout_model.ATTACHMENT) == result.layout
    layout = layout_model.load(result.layout)
    assert {f.ref for f in layout.fields} == {f"#/texts/{i}" for i in range(4)}
    words, original = _words(result.layout_pdf), _words(styled_pdf())
    assert [w for _, w in words[770]] == ["Paziente:", "[PERSONA_1]"]
    assert words[770][1][0] == original[770][1][0]  # where "Mario" was
    assert words[800] == pytest.approx(original[800], abs=0.5)
    page = extract(result.layout_pdf, {1}, graphics=True)
    fonts_used = {(s.bold, s.italic, s.color) for s in page.styles}
    assert (False, True, (255, 0, 0)) in fonts_used and (
        True,
        False,
        (0, 0, 0),
    ) in fonts_used
    shapes = sorted((g.kind, g.x0, g.y0) for g in page.pages[1].graphics)
    assert ("line", X, 720) in shapes and ("rect", X, 600) in shapes
    clear = " ".join(text_of(result.layout_pdf))
    for value in VALUES:
        assert value not in clear and value not in result.layout.decode()
    assert result.metrics is not None
    counters = result.metrics["layout"]
    assert counters["fields_styled"] == 4 and counters["graphics_kept"] == 2
    assert counters["graphics_dropped_barcode"] == 20


def test_restore_puts_the_values_back_at_their_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _ = _run(monkeypatch)
    assert result.layout_pdf is not None and result.encrypted_map is not None
    content = vault.decrypt_map(result.encrypted_map, KEY)
    assert content["sha256_layout"] == layout_model.fingerprint(
        layout_model.load(result.layout or b"")
    )

    restored = restore_pdf(result.layout_pdf, result.encrypted_map, KEY)

    original = _words(styled_pdf())
    assert _words(restored)[770] == pytest.approx(original[770], abs=0.5)
    assert "Mario Rossi" in " ".join(text_of(restored))
    # the structure JSON alone has no layout: drawn in the boxes, as before
    plain = restore_pdf(result.structure or b"", result.encrypted_map, KEY)
    assert "Mario Rossi" in " ".join(text_of(plain))


def test_a_tampered_layout_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = _run(monkeypatch)
    assert result.structure is not None and result.layout is not None
    data = json.loads(result.layout)
    data["fields"][1]["runs"][0]["x"] += 10
    forged = attach(
        attach(
            render_pdf(structure.load(result.structure)),
            structure.ATTACHMENT,
            result.structure,
        ),
        layout_model.ATTACHMENT,
        json.dumps(data).encode(),
    )
    assert result.encrypted_map is not None

    with pytest.raises(StructureError, match="layout"):
        restore_pdf(forged, result.encrypted_map, KEY)
    broken = attach(
        attach(
            render_pdf(structure.load(result.structure)),
            structure.ATTACHMENT,
            result.structure,
        ),
        layout_model.ATTACHMENT,
        b"{}",
    )
    with pytest.raises(StructureError, match="layout"):
        restore_pdf(broken, result.encrypted_map, KEY)


def test_styles_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    off = {
        "output": {
            "layout_pdf": True,
            "layout_fidelity": {"style": False, "graphics": True},
        }
    }

    result, _ = _run(monkeypatch, off)

    assert result.layout_pdf is not None and result.layout is None
    assert attachment(result.layout_pdf, layout_model.ATTACHMENT) is None
    assert result.encrypted_map is not None
    assert "sha256_layout" not in vault.decrypt_map(result.encrypted_map, KEY)


def test_graphics_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    no_lines = {
        "output": {
            "layout_pdf": True,
            "layout_fidelity": {"style": True, "graphics": False},
        }
    }

    result, _ = _run(monkeypatch, no_lines)

    assert result.layout is not None
    assert all(not p.graphics for p in layout_model.load(result.layout).pages)


def test_an_unreadable_pdf_falls_back_to_the_boxes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise pdfium.PdfiumError("broken")

    monkeypatch.setattr(layout_pdf, "extract", broken)

    result, _ = _run(monkeypatch)

    assert result.layout_pdf is not None and result.layout is None
    assert "layout_style_unavailable" in result.report["warnings"]


def test_layout_never_logs_values(
    monkeypatch: pytest.MonkeyPatch, log_stream: io.StringIO
) -> None:
    result, _ = _run(monkeypatch)

    logs = log_stream.getvalue()
    metrics = json.dumps(result.metrics)
    for value in VALUES:
        assert value not in logs and value not in metrics
    assert "Helvetica" not in (result.layout or b"").decode()


def test_shapes_inside_pictures_are_not_drawn(monkeypatch: pytest.MonkeyPatch) -> None:
    convert = convert_lines()

    def with_picture(*args: object) -> Conversion:
        conversion = convert(*args)
        conversion.document.add_picture(
            prov=ProvenanceItem(
                page_no=1,
                bbox=BoundingBox(
                    l=290, t=540, r=370, b=490, coord_origin=CoordOrigin.BOTTOMLEFT
                ),
                charspan=(0, 0),
            )
        )
        return conversion

    monkeypatch.setattr(native_pdf, "convert", with_picture)
    cfg = pipeline_config(LAYOUT)
    result = asyncio.run(
        anonymize(
            "referto.pdf", styled_pdf(), cfg, FakeVLM(cfg, IdealModel(VALUES)), KEY
        )
    )

    assert result.metrics is not None
    counters = result.metrics["layout"]
    assert counters["graphics_dropped_picture"] == 20
    assert counters["graphics_dropped_barcode"] == 0


@pytest.mark.parametrize("kind", ["text", "cells", "picture"])
def test_a_tax_code_in_boxes_is_one_tag_on_a_clean_row(
    monkeypatch: pytest.MonkeyPatch, log_stream: io.StringIO, kind: str
) -> None:
    def convert(data: bytes, pages: list[int], c: NativePdf) -> Conversion:
        return native_pdf.finish(boxed_structure(kind=kind, missing=3), data, pages, c)

    monkeypatch.setattr(native_pdf, "convert", convert)
    cfg = pipeline_config(LAYOUT)
    client = FakeVLM(cfg, IdealModel({BOXED_CF: "CODICE_FISCALE"}))

    result = asyncio.run(anonymize("modulo.pdf", boxed_pdf(), cfg, client, KEY))

    assert "[CODICE_FISCALE_1]" in result.markdown
    assert BOXED_CF not in result.markdown.replace("\n", "").replace(" ", "")
    assert result.report["layout"] == "pdf" and result.layout_pdf is not None
    clear = "".join("".join(text_of(result.layout_pdf)).split())
    assert BOXED_CF[:8] not in clear and "[CODICE_FISCALE_1]" in clear
    frames = extract(result.layout_pdf, {1}, graphics=True).pages[1].graphics
    assert not [g for g in frames if g.stroke is not None and 690 <= g.y0 <= 712]
    assert result.metrics is not None
    # the scale has 7 boxes left: not even a row
    assert result.metrics["extraction"]["char_rows"] == {"compacted": 1, "skipped": 0}
    assert result.encrypted_map is not None
    restored = "".join(
        text_of(restore_pdf(result.layout_pdf, result.encrypted_map, KEY))
    )
    assert BOXED_CF in restored
    assert BOXED_CF not in log_stream.getvalue()
