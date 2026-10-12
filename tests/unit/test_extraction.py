import asyncio
import ctypes
import io
import os
from pathlib import Path
from typing import get_args

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
from PIL import Image, ImageDraw

from anonymization_pipeline.domain import Document, Page
from anonymization_pipeline.extraction import extract, extract_page, native_pdf
from anonymization_pipeline.ingest import load
from anonymization_pipeline.pipeline_config import PipelineConfig, request_overrides
from anonymization_pipeline.pipeline_config.schema import (
    LayoutModel,
    NativePdf,
    TableModel,
)
from anonymization_pipeline.vlm import VLMResponse
from tests.pipeline import (
    FIXTURES,
    FakeVLM,
    IdealModel,
    fake_convert,
    pipeline_config,
    width,
)

LINES = [
    "Referto cardiologico del paziente Mario Rossi",
    "Codice fiscale RSSMRA80A01H501U",
    "Controllo in paziente con fibrillazione atriale",
]


def pdf_with_text(lines: list[tuple[str, str]]) -> bytes:
    """One-page PDF with a line per entry; style: '' normal, 'invisible' (render mode 3), 'white'."""
    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(595, 842)
    y = 800
    for text, style in lines:
        obj = pdfium_c.FPDFPageObj_NewTextObj(pdf.raw, b"Helvetica", ctypes.c_float(12))
        encoded = (text + "\x00").encode("utf-16-le")
        pdfium_c.FPDFText_SetText(
            obj, (pdfium_c.FPDF_WCHAR * (len(encoded) // 2)).from_buffer_copy(encoded)
        )
        pdfium_c.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 50, y)
        if style == "invisible":
            pdfium_c.FPDFTextObj_SetTextRenderMode(
                obj, pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE
            )
        if style == "white":
            pdfium_c.FPDFPageObj_SetFillColor(obj, 255, 255, 255, 255)
        pdfium_c.FPDFPage_InsertObject(page.raw, obj)
        y -= 20
    pdfium_c.FPDFPage_GenerateContent(page.raw)
    buffer = io.BytesIO()
    pdf.save(buffer)
    return buffer.getvalue()


def _lined_page() -> Image.Image:
    """400x1000 page with black "text lines" 20 px high every 50 px (from y=10)."""
    image = Image.new("RGB", (400, 1000), "white")
    draw = ImageDraw.Draw(image)
    for y in range(10, 1000, 50):
        draw.rectangle((20, y, 380, y + 19), fill="black")
    return image


def _pages(data: bytes, cfg: PipelineConfig | None = None) -> list[tuple[bool, str]]:
    doc = load("p.pdf", data, cfg or pipeline_config())
    return [(p.native, p.reason) for p in doc.pages]


def test_one_call_per_page_with_the_whole_image() -> None:
    cfg = pipeline_config()
    client = FakeVLM(cfg, lambda *a: VLMResponse(text="```markdown\n# Referto\n```"))
    page = Page(index=0, image=_lined_page())

    assert asyncio.run(extract_page(page, client, cfg)) == []
    assert page.text == "# Referto"  # fence removed
    assert [(phase, n) for phase, _, n in client.calls] == [("extraction", 1)]


def test_truncated_and_empty_warnings() -> None:
    cfg = pipeline_config()
    client = FakeVLM(cfg, lambda *a: VLMResponse(text="", truncated=True))
    page = Page(index=4, image=_lined_page())

    assert asyncio.run(extract_page(page, client, cfg)) == [
        "extraction_truncated:page_5",
        "extraction_empty:page_5",
    ]
    assert len(client.calls) == 1  # no retry


def test_image_resized_to_max_side() -> None:
    cfg = pipeline_config({"images": {"max_side_px": 300}})
    seen: list[int] = []
    client = FakeVLM(
        cfg, lambda phase, s, u, images, schema: seen.append(width(images[0])) or "ok"
    )

    asyncio.run(extract_page(Page(index=0, image=_lined_page()), client, cfg))

    assert seen == [120]  # 400x1000 -> longest side 300


def test_native_or_scanned_page() -> None:
    assert _pages((FIXTURES / "referto_pdf_nativo.pdf").read_bytes()) == [(True, "")]
    assert _pages((FIXTURES / "referto_scansione.pdf").read_bytes()) == [
        (False, "little_text")
    ]
    assert _pages(pdf_with_text([(line, "") for line in LINES])) == [(True, "")]
    cfg = pipeline_config(default_extraction="vlm")
    assert _pages(pdf_with_text([(line, "") for line in LINES]), cfg) == [
        (False, "vlm_engine")
    ]


def test_hidden_text_sends_the_page_to_the_vlm() -> None:
    # text that cannot be seen must not end up in the Markdown: only a text-layer reader sees it
    visible = [(line, "") for line in LINES]
    assert _pages(pdf_with_text([*visible, ("Mario Rossi", "invisible")])) == [
        (False, "hidden_text")
    ]
    assert _pages(pdf_with_text([*visible, ("Mario Rossi", "white")])) == [
        (False, "hidden_text")
    ]


def _doc() -> Document:
    return Document(
        kind="pdf",
        source=b"%PDF",
        pages=[
            Page(index=0, image=Image.new("RGB", (400, 300), "white"), native=True),
            Page(
                index=1,
                image=Image.new("RGB", (410, 300), "white"),
                native=False,
                reason="little_text",
            ),
        ],
    )


def test_hybrid_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        native_pdf,
        "convert",
        fake_convert(lambda i: f"# Pagina {i + 1} da docling"),
    )
    doc, cfg = _doc(), pipeline_config()
    client = FakeVLM(cfg, IdealModel({}, markdown={410: "Pagina 2 dal VLM"}))

    assert asyncio.run(extract(doc, client, cfg)) == []
    assert [(p.text, p.extracted_with) for p in doc.pages] == [
        ("# Pagina 1 da docling", "docling"),
        ("Pagina 2 dal VLM", "vlm"),
    ]
    assert client.phases() == ["extraction"]  # only the scanned page goes to the VLM


def test_without_docling_every_page_goes_to_the_vlm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*args: object) -> dict[int, str]:
        raise native_pdf.DoclingUnavailable("models missing")

    monkeypatch.setattr(native_pdf, "convert", missing)
    doc, cfg = _doc(), pipeline_config()
    client = FakeVLM(
        cfg, IdealModel({}, markdown={400: "Pagina 1 dal VLM", 410: "Pagina 2 dal VLM"})
    )

    assert asyncio.run(extract(doc, client, cfg)) == ["docling_unavailable"]
    assert [p.extracted_with for p in doc.pages] == ["vlm", "vlm"]
    assert client.phases().count("extraction") == 2


def test_text_documents_are_not_extracted() -> None:
    cfg = pipeline_config()
    client = FakeVLM(cfg, IdealModel({}))

    assert (
        asyncio.run(
            extract(Document(kind="text", pages=[Page(index=0, text="x")]), client, cfg)
        )
        == []
    )
    assert client.calls == []


def test_missing_models(tmp_path: Path) -> None:
    c = pipeline_config(docling_models_dir=str(tmp_path)).phases.extraction.native_pdf

    with pytest.raises(native_pdf.DoclingUnavailable):
        native_pdf.convert(b"%PDF", [0], c)


def test_every_layout_model_has_a_docling_preset() -> None:
    assert set(native_pdf.LAYOUT_MODELS) == set(get_args(LayoutModel))
    assert set(native_pdf.TABLE_MODELS) == set(get_args(TableModel))


def _docling_options(tmp_path: Path, content: str) -> NativePdf:
    path = tmp_path / "docling.yaml"
    path.write_text(content)
    return pipeline_config(
        docling_models_dir=str(tmp_path / "models"), docling_file=str(path)
    ).phases.extraction.native_pdf


@pytest.mark.parametrize(
    ("content", "folders"),
    [
        (
            "{}",
            [
                "docling-project--docling-layout-heron",
                "docling-project--docling-models",
            ],
        ),
        (
            "layout: {model: egret_large}\ntables: {model: tableformer_v2}\n",
            [
                "docling-project--docling-layout-egret-large",
                "docling-project--TableFormerV2",
            ],
        ),
        ("tables: {enabled: false}\n", ["docling-project--docling-layout-heron"]),
    ],
)
def test_required_models_follow_the_docling_file(
    tmp_path: Path, content: str, folders: list[str]
) -> None:
    c = _docling_options(tmp_path, content)

    assert native_pdf.required_models(c) == folders
    for folder in folders:
        assert not native_pdf.models_present(c)
        (tmp_path / "models" / folder).mkdir(parents=True)
    assert native_pdf.models_present(c)


@pytest.mark.docling
def test_docling_file_reaches_docling(tmp_path: Path) -> None:
    if not native_pdf.DOCLING_INSTALLED:
        pytest.skip("docling is missing")
    c = _docling_options(
        tmp_path,
        "layout: {model: egret_medium, batch_size: 2}\n"
        "tables: {mode: fast, cell_matching: false}\n"
        "timeout_s: 60\nthreads: 2\n",
    )

    options = native_pdf.pipeline_options(c.docling, c.models_dir)

    assert options.do_ocr is False
    assert options.table_structure_options.do_cell_matching is False
    assert options.table_structure_options.mode.value == "fast"
    assert options.layout_options.model_spec.repo_id.endswith("layout-egret-medium")
    assert (options.layout_batch_size, options.document_timeout) == (2, 60)
    assert options.accelerator_options.num_threads == 2


@pytest.mark.docling
def test_real_docling() -> None:
    """Image with the 'docling' extra and the models in ANONPIP_DOCLING_MODELS_DIR (or the default)."""
    folder = os.environ.get("ANONPIP_DOCLING_MODELS_DIR", "/models/docling")
    c = pipeline_config(docling_models_dir=folder).phases.extraction.native_pdf
    if not native_pdf.DOCLING_INSTALLED or not native_pdf.models_present(c):
        pytest.skip("docling or its models are missing")

    markdown = native_pdf.convert(
        (FIXTURES / "referto_pdf_nativo.pdf").read_bytes(), [0], c
    )[0][0]

    assert (
        "## VISITA CARDIOLOGICA" in markdown
        and "SPSFNC72M67G273V" in markdown
        and "|---" in markdown
    )


def test_engine_chosen_per_request() -> None:
    def engine(cfg: PipelineConfig) -> str:
        return cfg.phases.extraction.native_pdf.engine

    assert engine(pipeline_config()) == "docling"
    assert engine(pipeline_config(default_extraction="vlm")) == "vlm"
    assert engine(pipeline_config(request_overrides(extraction="vlm"))) == "vlm"
    with pytest.raises(ValueError):
        pipeline_config(request_overrides(extraction="ocr"))
