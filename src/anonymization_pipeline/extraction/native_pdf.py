"""Extraction of the native pages of a PDF with docling.

Exact text from the PDF text layer, structure (headings, lists, tables) from the layout and
TableFormer models, on CPU, without OCR (scanned pages go to the VLM).

The options come from the docling file (``phases.extraction.native_pdf.docling``). The models
live in ``phases.extraction.native_pdf.models_dir`` and are loaded without network;
``python -m anonymization_pipeline.model_store`` downloads the ones the docling file chooses.
Without docling (image without the ``docling`` extra) or without the models:
``DoclingUnavailable``, and the pipeline uses the VLM for the native pages too.
"""

import functools
import io
import logging
import threading
from pathlib import Path
from typing import Any

from docling_core.types.doc import ContentLayer, DoclingDocument

from anonymization_pipeline.pipeline_config.schema import Docling, NativePdf

try:
    from docling.backend.docling_parse_backend import (
        ThreadedDoclingParseDocumentBackend,
    )
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.backend_options import PdfBackendOptions
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import (
        LayoutObjectDetectionOptions,
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
        TableStructureV2Options,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.models.utils.hf_model_download import download_hf_model
    from docling.utils.model_downloader import download_models

    DOCLING_INSTALLED = True
except ImportError:  # image without the 'docling' extra
    DOCLING_INSTALLED = False

logger = logging.getLogger(__name__)

# layout model of the docling file -> (docling preset, Hugging Face repository)
LAYOUT_MODELS = {
    "heron": ("layout_heron_default", "docling-project/docling-layout-heron"),
    "heron_101": ("layout_heron_101", "docling-project/docling-layout-heron-101"),
    "egret_medium": (
        "layout_egret_medium",
        "docling-project/docling-layout-egret-medium",
    ),
    "egret_large": ("layout_egret_large", "docling-project/docling-layout-egret-large"),
    "egret_xlarge": (
        "layout_egret_xlarge",
        "docling-project/docling-layout-egret-xlarge",
    ),
}
# table model of the docling file -> subfolder of models_dir (docling's own layout)
TABLE_MODELS = {
    "tableformer": "docling-project--docling-models",
    "tableformer_v2": "docling-project--TableFormerV2",
}
IMAGE_PLACEHOLDER = "[IMMAGINE]"

_LOCK = (
    threading.Lock()
)  # one document at a time per process: the models are not thread-safe


class DoclingUnavailable(RuntimeError):
    """docling or its models are missing, or the conversion failed."""


def _layout_folder(d: Docling) -> str:
    return LAYOUT_MODELS[d.layout.model][1].replace("/", "--")


def required_models(c: NativePdf) -> list[str]:
    """Subfolders of ``models_dir`` the docling file needs: layout, plus tables when on."""
    d = c.docling
    return [_layout_folder(d)] + (
        [TABLE_MODELS[d.tables.model]] if d.tables.enabled else []
    )


def models_present(c: NativePdf) -> bool:
    return all((Path(c.models_dir) / m).is_dir() for m in required_models(c))


def download(c: NativePdf) -> None:
    """Download into the models folder only the models the docling file chooses.

    Raises:
        DoclingUnavailable: docling is not installed.
    """
    if not DOCLING_INSTALLED:
        raise DoclingUnavailable(
            "docling not installed (image without the 'docling' extra)"
        )
    d = c.docling
    download_hf_model(
        repo_id=LAYOUT_MODELS[d.layout.model][1],
        local_dir=Path(c.models_dir) / _layout_folder(d),
    )
    download_models(
        output_dir=Path(c.models_dir),
        with_layout=False,
        with_tableformer=d.tables.enabled and d.tables.model == "tableformer",
        with_tableformer_v2=d.tables.enabled and d.tables.model == "tableformer_v2",
        with_code_formula=False,
        with_picture_classifier=False,
        with_rapidocr=False,
    )


def pipeline_options(d: Docling, models_dir: str) -> Any:
    """docling's ``PdfPipelineOptions`` from the docling file: no OCR, CPU only."""
    tables = (
        TableStructureOptions(
            do_cell_matching=d.tables.cell_matching,
            mode=TableFormerMode(d.tables.mode),
        )
        if d.tables.model == "tableformer"
        else TableStructureV2Options(do_cell_matching=d.tables.cell_matching)
    )
    return PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=d.tables.enabled,
        table_structure_options=tables,
        table_batch_size=d.tables.batch_size,
        layout_options=LayoutObjectDetectionOptions.from_preset(
            LAYOUT_MODELS[d.layout.model][0],
            create_orphan_clusters=d.layout.create_orphan_clusters,
            keep_empty_clusters=d.layout.keep_empty_clusters,
            skip_cell_assignment=d.layout.skip_cell_assignment,
        ),
        layout_batch_size=d.layout.batch_size,
        force_backend_text=d.force_backend_text,
        use_reading_order_separators=d.reading_order_separators,
        document_timeout=d.timeout_s,
        artifacts_path=models_dir,
        accelerator_options=AcceleratorOptions(device="cpu", num_threads=d.threads),
    )


@functools.lru_cache(maxsize=2)
def _converter(d: Docling, models_dir: str) -> Any:
    """Converter loaded once per process (per docling options and models folder)."""
    backend = (
        PyPdfiumDocumentBackend
        if d.pdf.parser == "pypdfium2"
        else ThreadedDoclingParseDocumentBackend
    )
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options(d, models_dir),
                backend=backend,
                backend_options=PdfBackendOptions(
                    enforce_same_font=d.pdf.enforce_same_font
                ),
            )
        }
    )


def convert(
    data: bytes, pages: list[int], c: NativePdf
) -> tuple[dict[int, str], DoclingDocument]:
    """Markdown of ``pages`` (zero-based) of the PDF, and the docling document it comes from.

    Headers and footers included unless the docling file excludes them, text that docling
    classifies as invisible or background excluded; images become ``[IMMAGINE]``.

    Raises:
        DoclingUnavailable: docling or its models are missing, or the conversion failed.
    """
    if not DOCLING_INSTALLED:
        raise DoclingUnavailable(
            "docling not installed (image without the 'docling' extra)"
        )
    if not models_present(c):
        raise DoclingUnavailable(
            f"docling models missing in {c.models_dir} (download them with `just models`)"
        )
    with _LOCK:
        try:
            result = _converter(c.docling, c.models_dir).convert(
                DocumentStream(name="document.pdf", stream=io.BytesIO(data)),
                raises_on_error=True,
            )
        except (
            Exception
        ) as exc:  # docling raises many unrelated types: all mean "not converted"
            raise DoclingUnavailable(
                f"docling conversion failed: {type(exc).__name__}"
            ) from exc
    layers = {ContentLayer.BODY}
    if c.docling.furniture:
        layers.add(ContentLayer.FURNITURE)
    # no escaping: "&", "<", "_" stay as in the document (values must be found in the text)
    markdown = {
        i: result.document.export_to_markdown(
            page_no=i + 1,
            included_content_layers=layers,
            image_placeholder=IMAGE_PLACEHOLDER,
            escape_html=False,
            escape_underscores=False,
        ).strip()
        for i in pages
    }
    return markdown, result.document
