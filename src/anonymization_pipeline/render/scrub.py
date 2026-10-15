"""Document-level clean-up of a redacted PDF, with pypdf (BSD-3).

The pages are copied into a new document: only what the pages reference survives, so the
catalog-level content of the original (metadata and XMP, outlines, named JavaScript, embedded
files, the open action, forms, the structure tree with its alternate texts) and every older
revision are gone. On the pages, annotations, thumbnails and private data are removed. The file is
written in full, never as an incremental update.
"""

import io

from pypdf import PdfReader, PdfWriter

PRODUCER = "anonymization-pipeline"
# page keys that can hold personal data or point to it
PAGE_KEYS = (
    "/Annots",
    "/Thumb",
    "/PieceInfo",
    "/Metadata",
    "/AA",
    "/B",
    "/StructParents",
    "/Tabs",
)


def scrub(data: bytes) -> bytes:
    """The PDF rewritten with only its pages, without annotations, metadata or history."""
    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter()
    for page in reader.pages:
        for key in PAGE_KEYS:
            page.pop(key, None)
        writer.add_page(page)
    writer.add_metadata({"/Producer": PRODUCER})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
