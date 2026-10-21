"""End-to-end analysis of a PDF: text layers -> findings -> redaction boxes."""

import dataclasses
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium

from anonymization_core.detect import visual
from anonymization_core.detect.ensemble import (
    Finding,
    analyze_layers,
)
from anonymization_core.detect.ner import NerEngine
from anonymization_core.geometry import (  # noqa: F401 - re-exported
    area,
    from_display,
    intersects,
    overlap_area,
    to_display,
    union,
)
from anonymization_core.layers import (
    Rect,
    TextLayer,
    Word,
)
from anonymization_core.pdf_pages import (
    PDFIUM_LOCK,
    PageView,
    open_pages,
)
from anonymization_core.reader import (
    Region,
    TextReader,
    derotate_rect,
)
from anonymization_core.taxonomy import LABELS

Progress = Callable[[str, float], None]


@dataclass
class Box:
    """One redaction rectangle on one page."""

    page: int
    rect: Rect
    label: str
    text: str
    score: float
    sources: list[str]
    layer: str  # native | ocr | visual | manual
    redact: bool


@dataclass
class Analysis:
    """Result of the analysis of one document."""

    path: Path
    pages: list[dict]  # width, height, scanned
    boxes: list[Box] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    layers: list[TextLayer] = field(default_factory=list, repr=False)
    applied: list[tuple[int, Rect, str]] = field(default_factory=list, repr=False)
    # (page, region) of the tables and figures, page space (Markdown export)
    regions: list[tuple[int, Region]] = field(default_factory=list, repr=False)

    def search(self, term: str) -> list[Box]:
        """All occurrences of ``term`` (case-insensitive) in every text layer, as manual boxes.

        Args:
            term: Text to find; whitespace between words may vary.

        Returns:
            One box per line of every occurrence, marked as redacted.
        """
        t = term.strip()
        if len(t) < 2:
            return []
        rx = re.compile(r"\s+".join(re.escape(w) for w in t.split()), re.IGNORECASE)
        label = (
            "PERSON"
            if t[:1].isupper() and t.replace(" ", "").isalpha()
            else "MEDICAL_RECORD"
        )
        out = []
        for layer in self.layers:
            for m in rx.finditer(layer.text):
                for r in layer.rects(m.start(), m.end()):
                    out.append(
                        Box(
                            layer.page,
                            r,
                            label,
                            m.group(),
                            1.0,
                            ["manual-search"],
                            layer.source,
                            True,
                        )
                    )
        return out

    def snap(self, page: int, rect: Rect) -> Rect:
        """Grow a hand-drawn area to the full box of every character it touches.

        Redaction removes any glyph whose box intersects the area: without
        snapping, a box grazing a neighbouring line would erase part of it
        outside the black box.

        Args:
            page: Page number.
            rect: Area drawn by the user, in PDF points.

        Returns:
            The area extended to whole characters.
        """
        touched = [
            char
            for layer in self.layers
            if layer.page == page
            for token in layer.tokens
            if intersects(rect, token.rect)
            for char in token.chars
            if intersects(rect, char)
        ]
        return union([rect, *touched]) or rect

    def counts(self) -> dict[str, int]:
        """Distinct redacted values per label (what the UI chips show)."""
        seen: dict[str, set[str]] = {}
        for b in self.boxes:
            if b.redact:
                seen.setdefault(b.label, set()).add(
                    b.text.lower().strip() or f"{b.page}:{b.rect}"
                )
        return {k: len(v) for k, v in sorted(seen.items(), key=lambda kv: -len(kv[1]))}


def _no_progress(step: str, fraction: float) -> None:
    """Default progress callback."""


class Pipeline:
    """Text layers, detection and visual PII of a document.

    Args:
        reader: Text layers of the document (native text and OCR).
        ner: NER engine, or None for rules only.
        policy: Label -> redact overrides on top of the taxonomy defaults.
        ner_threshold: Minimum NER score for places, addresses, organizations.
        person_threshold: Minimum NER score for persons (recall first).
    """

    def __init__(
        self,
        reader: TextReader,
        ner: NerEngine | None,
        policy: dict[str, bool] | None = None,
        ner_threshold: float = 0.5,
        person_threshold: float = 0.4,
    ) -> None:
        self.reader = reader
        self.ner = ner
        self.policy = {k: v.redact for k, v in LABELS.items()} | (policy or {})
        self.ner_threshold = ner_threshold
        self.person_threshold = person_threshold

    @property
    def ready(self) -> bool:
        """True when every model is loaded."""
        return self.reader.ready and (self.ner is None or self.ner.ready)

    @staticmethod
    def _visual(
        views: list[PageView],
        pages: list[dict],
        ocr_words: dict[int, list[Word]],
        progress: Progress,
    ) -> list[visual.VisualFinding]:
        """Visual findings of every page, in page space (PDFium: hold the lock)."""
        vis: list[visual.VisualFinding] = []
        for view in views:
            pno = view.number
            progress("detect", 0.6 + 0.4 * pno / len(views))
            # found on the rendering: display space, turned into page space
            rendered = visual.codes(view)
            if pages[pno]["scanned"]:
                rendered += visual.unexplained_ink(view, ocr_words[pno])
                rendered += visual.stamp_from_text(pno, ocr_words[pno])
            else:
                # image and drawing positions are already in page space
                vis += visual.embedded_images(view)
                vis += visual.vector_marks(view)
            vis += [
                dataclasses.replace(v, rect=derotate_rect(pages[pno], v.rect))
                for v in rendered
            ]
        return vis

    def analyze(
        self, path: Path, progress: Progress = _no_progress, visual: bool = True
    ) -> Analysis:
        """Analyze a PDF.

        Args:
            path: PDF to analyze.
            progress: ``progress(step, fraction)``, called as work advances; it may raise to cancel.
            visual: Run the visual detectors (codes, signatures, stamps); off for
                the text-only export, where pictures become placeholders.

        Returns:
            The analysis, with one box per line of every finding.
        """
        # PDFium is held while reading and in the visual detectors, released for
        # NER: previews and uploads of other requests wait seconds, not minutes
        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(path)
        try:
            with PDFIUM_LOCK:
                views = open_pages(pdf)
                t0 = time.perf_counter()
                read = self.reader.read(
                    Path(path), views, lambda f: progress("read", f)
                )
            pages = [p.info for p in read]
            layers = [layer for p in read for layer in p.layers]
            ocr_words: dict[int, list[Word]] = {
                pno: p.ocr_words
                for pno, p in enumerate(read)
                if p.ocr_words is not None
            }
            t1 = time.perf_counter()
            progress("detect", 0.0)
            findings: list[Finding] = analyze_layers(
                layers, self.ner, self.ner_threshold, self.person_threshold
            )
            t2 = time.perf_counter()
            if visual:
                with PDFIUM_LOCK:
                    vis = self._visual(views, pages, ocr_words, progress)
            else:
                vis = []
            t3 = time.perf_counter()
        finally:
            with PDFIUM_LOCK:
                pdf.close()

        a = Analysis(
            Path(path),
            pages,
            timings={
                "text_layers": round(t1 - t0, 2),
                "detection": round(t2 - t1, 2),
                "visual": round(t3 - t2, 2),
            },
            layers=layers,
            regions=[
                (pno, region) for pno, p in enumerate(read) for region in p.regions
            ],
        )
        for f in findings:
            for r in f.layer.rects(f.start, f.end):
                a.boxes.append(
                    Box(
                        f.layer.page,
                        r,
                        f.label,
                        f.text,
                        f.score,
                        sorted(f.sources),
                        f.layer.source,
                        self.policy.get(f.label, True),
                    )
                )
        for v in _dedupe(vis):
            a.boxes.append(
                Box(
                    v.page,
                    v.rect,
                    v.label,
                    v.note,
                    v.score,
                    [v.source],
                    "visual",
                    self.policy.get(v.label, True),
                )
            )
        return a


def _dedupe(vis: list[visual.VisualFinding]) -> list[visual.VisualFinding]:
    """Codes found by zxing win over generic image/ink findings covering the same area."""
    out: list[visual.VisualFinding] = []
    rank = {"zxing": 0, "image": 1, "vector": 2, "stamp-text": 3, "colour": 4, "ink": 5}
    for v in sorted(vis, key=lambda v: rank.get(v.source, 9)):
        if any(
            o.page == v.page and overlap_area(o.rect, v.rect) > 0.5 * area(v.rect)
            for o in out
        ):
            continue
        out.append(v)
    return out
