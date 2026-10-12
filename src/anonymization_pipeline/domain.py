"""Data model shared by the pipeline phases."""

from dataclasses import dataclass, field
from typing import Any, Literal

from docling_core.types.doc import DoclingDocument
from PIL import Image

DocumentKind = Literal["pdf", "image", "text"]


@dataclass
class Page:
    """One page of a document.

    Attributes:
        index: Zero-based page index.
        image: RGB rendering; None for text files.
        text: Extracted Markdown (or the original text for text files).
        encoded_image: Image sent to detection.
        native: PDF only: readable and visible text layer (extractable with docling).
        reason: PDF only: why the page is not native.
        extracted_with: ``docling`` or ``vlm``.
    """

    index: int
    image: Image.Image | None = None
    text: str = ""
    encoded_image: bytes | None = field(default=None, repr=False)
    native: bool = False
    reason: str = ""
    extracted_with: str = ""


@dataclass
class Document:
    """A loaded document. ``source`` keeps the original PDF bytes for docling, ``docling`` the
    conversion of its native pages (IN CLEAR TEXT: only the layout structure is built from it)."""

    kind: DocumentKind
    pages: list[Page] = field(default_factory=list)
    source: bytes | None = field(default=None, repr=False)
    docling: DoclingDocument | None = field(default=None, repr=False)

    @property
    def visual(self) -> bool:
        return self.kind != "text"


@dataclass(frozen=True)
class Entity:
    """A sensitive value, in the exact form it has in the Markdown.

    ``page`` is where it was found (redaction applies to the whole document); ``source`` is the
    detector: rules | labels | ner | detection (VLM) | verification.
    """

    text: str
    category: str
    page: int
    source: str = "detection"


@dataclass
class Group:
    """Values that refer to the same entity: they get the same tag."""

    category: str
    values: list[Entity]
    tag: str = ""


@dataclass(frozen=True)
class Occurrence:
    """A replacement in the redacted Markdown: offsets in the redacted text and original value."""

    start: int
    end: int
    replacement: str
    original: str
    category: str


@dataclass(frozen=True)
class Residual:
    """Data left in clear text according to the verification (without the value).

    ``start``/``end`` are offsets in the returned Markdown, where the value is still in clear text.
    """

    category: str
    page: int
    start: int | None = None
    end: int | None = None


@dataclass
class Result:
    """Outcome of an anonymization.

    Attributes:
        name: Original file name (never logged: it often holds the patient's name).
        markdown: Redacted document.
        report: Counts and outcomes, without values.
        encrypted_map: Pseudonym map (pseudonym mode only).
        transcript: Transcript IN CLEAR TEXT (only on request, debugging).
        metrics: Times and performance, without values.
        tags: Positions of the replacements in the redacted Markdown, without values.
        occurrences: With the transcript: positions in the clear text too, and the values.
        layout_pdf: Censored PDF with the layout of the original, the structure attached.
        structure: Censored layout structure (DoclingDocument JSON).
    """

    name: str
    markdown: str
    report: dict[str, Any]
    encrypted_map: bytes | None = None
    transcript: str | None = None
    metrics: dict[str, Any] | None = None
    tags: list[dict[str, Any]] = field(default_factory=list)
    occurrences: list[dict[str, Any]] | None = None
    layout_pdf: bytes | None = field(default=None, repr=False)
    structure: bytes | None = field(default=None, repr=False)

    def output_names(self, stem: str | None = None) -> dict[str, str]:
        """File names of the outputs (unchanged from the original tool, for compatibility)."""
        stem = stem or self.name.rsplit(".", 1)[0]
        names = {"markdown": f"{stem}.anon.md", "report": f"{stem}.report.json"}
        if self.encrypted_map is not None:
            names["map"] = f"{stem}.mappa.enc"
        if self.metrics is not None:
            names["metrics"] = f"{stem}.metriche.json"
        if self.transcript is not None:
            names["transcript"] = f"{stem}.trascrizione.md"
        if self.layout_pdf is not None:
            names["layout_pdf"] = f"{stem}.anon.pdf"
        if self.structure is not None:
            names["structure"] = f"{stem}.anon.docling.json"
        return names
