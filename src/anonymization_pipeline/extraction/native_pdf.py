"""Extraction of the native pages of a PDF with docling.

Exact text from the PDF text layer, structure (headings, lists, tables) from the layout and
TableFormer models, on CPU, without OCR (scanned pages go to the VLM).

The models live in ``phases.extraction.native_pdf.models_dir`` and are loaded without network;
``python -m anonymization_pipeline.model_store`` downloads them. Without docling (image without
the ``docling`` extra) or without the models: ``DoclingUnavailable``, and the pipeline uses the
VLM for the native pages too.
"""

import functools
import io
import logging
import threading
from pathlib import Path
from typing import Any

from anonymization_pipeline.pipeline_config.schema import NativePdf

try:
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.utils.model_downloader import download_models
    from docling_core.types.doc.document import ContentLayer

    DOCLING_INSTALLED = True
except ImportError:  # image without the 'docling' extra
    DOCLING_INSTALLED = False

logger = logging.getLogger(__name__)

# subfolders of models_dir that must exist (layout always, TableFormer with tables)
LAYOUT_MODEL = "docling-project--docling-layout-heron"
TABLES_MODEL = "docling-project--docling-models"
IMAGE_PLACEHOLDER = "[IMMAGINE]"

_LOCK = (
    threading.Lock()
)  # one document at a time per process: the models are not thread-safe


class DoclingUnavailable(RuntimeError):
    """docling or its models are missing, or the conversion failed."""


def models_present(c: NativePdf) -> bool:
    required = [LAYOUT_MODEL] + ([TABLES_MODEL] if c.tables else [])
    return all((Path(c.models_dir) / m).is_dir() for m in required)


def download(c: NativePdf) -> None:
    """Download into the models folder only the models that are needed (layout, tables if on).

    Raises:
        DoclingUnavailable: docling is not installed.
    """
    if not DOCLING_INSTALLED:
        raise DoclingUnavailable(
            "docling not installed (image without the 'docling' extra)"
        )
    download_models(
        output_dir=Path(c.models_dir),
        with_layout=True,
        with_tableformer=c.tables,
        with_code_formula=False,
        with_picture_classifier=False,
        with_rapidocr=False,
    )


@functools.lru_cache(maxsize=2)
def _converter(models_dir: str, tables: bool, threads: int | None) -> Any:
    """Converter loaded once per process (per models folder and options)."""
    options = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=tables,
        artifacts_path=models_dir,
        accelerator_options=AcceleratorOptions(device="cpu", num_threads=threads or 4),
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def convert(data: bytes, pages: list[int], c: NativePdf) -> dict[int, str]:
    """Markdown of ``pages`` (zero-based) of the PDF.

    Headers and footers included, text that docling classifies as invisible or background
    excluded; images become ``[IMMAGINE]``.

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
    converter = _converter(c.models_dir, c.tables, c.threads)
    with _LOCK:
        try:
            result = converter.convert(
                DocumentStream(name="document.pdf", stream=io.BytesIO(data)),
                raises_on_error=True,
            )
        except (
            Exception
        ) as exc:  # docling raises many unrelated types: all mean "not converted"
            raise DoclingUnavailable(
                f"docling conversion failed: {type(exc).__name__}"
            ) from exc
    layers = {ContentLayer.BODY, ContentLayer.FURNITURE}
    # no escaping: "&", "<", "_" stay as in the document (values must be found in the text)
    return {
        i: result.document.export_to_markdown(
            page_no=i + 1,
            included_content_layers=layers,
            image_placeholder=IMAGE_PLACEHOLDER,
            escape_html=False,
            escape_underscores=False,
        ).strip()
        for i in pages
    }
