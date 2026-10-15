"""Page classification: how much of a page is covered by raster images."""

from typing import Any

import pypdfium2.raw as pdfium_c

from anonymization_pipeline.pdf.geometry import area, intersect

# images over this fraction of the page area: a scan (or a photo) whose text must be read by OCR
SCANNED_COVERAGE = 0.5


def image_coverage(page: Any) -> float:
    """Fraction of the page covered by raster images (overlaps counted once per image).

    Args:
        page: An open ``pypdfium2.PdfPage`` (the caller holds ``PDFIUM_LOCK``).
    """
    left, bottom, right, top = page.get_mediabox()
    page_rect = (left, bottom, right, top)
    covered = 0.0
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=8):
        x0, y0, x1, y1 = obj.get_bounds()
        covered += area(intersect((x0, y0, x1, y1), page_rect))
    return min(1.0, covered / max(area(page_rect), 1.0))


def is_scanned(page: Any) -> bool:
    """True when raster images cover more than half of the page."""
    return image_coverage(page) > SCANNED_COVERAGE
