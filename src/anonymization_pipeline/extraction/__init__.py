"""Extraction: every page -> faithful Markdown.

- Native pages of a PDF (``Page.native``, see ingest) with engine ``docling``: docling
  (``native_pdf.py``), in a thread, in parallel with the VLM.
- All the others (scans, images, pages with many images or hidden text), and the native ones when
  docling is unavailable: the VLM, one call per page with the whole image. When the transcript is
  cut by the token limit (or is empty) the report gets a warning for the page.
"""

import asyncio
import logging

from anonymization_pipeline import metrics
from anonymization_pipeline.domain import Document, Page
from anonymization_pipeline.extraction import native_pdf
from anonymization_pipeline.pipeline_config import PipelineConfig, render
from anonymization_pipeline.vlm import VLMClient, encode_image, gather_or_cancel
from anonymization_pipeline.vlm.responses import strip_fence

logger = logging.getLogger(__name__)


def visual_elements(cfg: PipelineConfig) -> str:
    return "\n".join(
        f"  - {name}: {desc}"
        for name, desc in cfg.phases.extraction.visual_elements.items()
    )


async def extract_page(page: Page, client: VLMClient, cfg: PipelineConfig) -> list[str]:
    """Fill ``page.text`` with the VLM transcript; return the warnings."""
    if page.image is None:
        return []
    system = render(cfg, "extraction", "system", visual_elements=visual_elements(cfg))
    with metrics.on_page(page.index):
        response = await client.call(
            "extraction",
            system,
            render(cfg, "extraction", "user"),
            [encode_image(page.image, cfg)],
        )
    page.text = strip_fence(response.text).strip()
    page.extracted_with = "vlm"
    warnings = []
    if response.truncated:
        warnings.append(f"extraction_truncated:page_{page.index + 1}")
    if not page.text:
        warnings.append(f"extraction_empty:page_{page.index + 1}")
    return warnings


async def _docling(
    doc: Document, cfg: PipelineConfig
) -> tuple[dict[int, str], list[str]]:
    """Markdown of the native pages (``doc.docling`` keeps the conversion) and the warnings."""
    native = [p.index for p in doc.pages if p.native]
    settings = cfg.phases.extraction.native_pdf
    if not native or doc.source is None or settings.engine != "docling":
        return {}, []
    try:
        with metrics.measure(
            "extraction.docling"
        ):  # the whole document in one conversion
            markdown, doc.docling = await asyncio.to_thread(
                native_pdf.convert, doc.source, native, settings
            )
    except native_pdf.DoclingUnavailable as exc:
        logger.warning("Native pages extracted with the VLM: %s", exc)
        return {}, ["docling_unavailable"]
    return {
        i: t for i, t in markdown.items() if t
    }, []  # page empty for docling: the VLM redoes it


async def extract(doc: Document, client: VLMClient, cfg: PipelineConfig) -> list[str]:
    """Transcribe every page and return the warnings.

    docling for the native pages, the VLM for the others (in parallel, the VLM within
    ``backend.concurrency``); when docling is unavailable, the VLM for the native pages too.
    """
    if not doc.visual:
        return []
    others = [p for p in doc.pages if not p.native]
    (markdown, warnings), results = await asyncio.gather(
        _docling(doc, cfg),
        gather_or_cancel(extract_page(p, client, cfg) for p in others),
    )
    for page in doc.pages:
        if page.index in markdown:
            page.text, page.extracted_with = markdown[page.index], "docling"
    fallback = [p for p in doc.pages if p.native and p.index not in markdown]
    results += await gather_or_cancel(extract_page(p, client, cfg) for p in fallback)
    return warnings + [w for r in results for w in r]
