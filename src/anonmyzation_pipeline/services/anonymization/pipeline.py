"""End-to-end analysis of a PDF: text layers -> findings -> redaction boxes."""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from anonmyzation_pipeline.services.anonymization.detect import visual
from anonmyzation_pipeline.services.anonymization.detect.ensemble import (
    Finding,
    analyze_layers,
)
from anonmyzation_pipeline.services.anonymization.detect.ner import NerEngine
from anonmyzation_pipeline.services.anonymization.layers import (
    Rect,
    TextLayer,
    native_layer,
    ocr_layer,
    page_number,
)
from anonmyzation_pipeline.services.anonymization.ocr import OcrEngine, Word
from anonmyzation_pipeline.services.anonymization.taxonomy import LABELS

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
        area = pymupdf.Rect(rect)
        grown = pymupdf.Rect(area)
        for layer in self.layers:
            if layer.page != page:
                continue
            for token in layer.tokens:
                if not area.intersects(token.rect):
                    continue
                for char in token.chars:
                    if area.intersects(char):
                        grown |= char
        return (grown.x0, grown.y0, grown.x1, grown.y1)

    def counts(self) -> dict[str, int]:
        """Distinct redacted values per label (what the UI chips show)."""
        seen: dict[str, set[str]] = {}
        for b in self.boxes:
            if b.redact:
                seen.setdefault(b.label, set()).add(
                    b.text.lower().strip() or f"{b.page}:{b.rect}"
                )
        return {k: len(v) for k, v in sorted(seen.items(), key=lambda kv: -len(kv[1]))}


def is_scanned(page: pymupdf.Page) -> bool:
    """True when raster images cover more than half of the page."""
    area = page.rect.width * page.rect.height
    covered = sum(
        (pymupdf.Rect(i["bbox"]) & page.rect).get_area() for i in page.get_image_info()
    )
    return covered > 0.5 * area


def _no_progress(step: str, fraction: float) -> None:
    """Default progress callback."""


class Pipeline:
    """Text layers, detection and visual PII of a document.

    Args:
        ocr: OCR engine used for scanned pages.
        ner: NER engine, or None for rules only.
        policy: Label -> redact overrides on top of the taxonomy defaults.
        ner_threshold: Minimum NER score for places, addresses, organizations.
        person_threshold: Minimum NER score for persons (recall first).
    """

    def __init__(
        self,
        ocr: OcrEngine,
        ner: NerEngine | None,
        policy: dict[str, bool] | None = None,
        ner_threshold: float = 0.5,
        person_threshold: float = 0.4,
    ) -> None:
        self.ocr = ocr
        self.ner = ner
        self.policy = {k: v.redact for k, v in LABELS.items()} | (policy or {})
        self.ner_threshold = ner_threshold
        self.person_threshold = person_threshold

    @property
    def ready(self) -> bool:
        """True when every model is loaded."""
        return self.ocr.ready and (self.ner is None or self.ner.ready)

    def analyze(self, path: Path, progress: Progress = _no_progress) -> Analysis:
        """Analyze a PDF.

        Args:
            path: PDF to analyze.
            progress: ``progress(step, fraction)``, called as work advances; it may raise to cancel.

        Returns:
            The analysis, with one box per line of every finding.
        """
        with pymupdf.open(path) as doc:
            t0 = time.perf_counter()
            layers: list[TextLayer] = []
            pages: list[dict] = []
            vis: list[visual.VisualFinding] = []
            ocr_words: dict[int, list[Word]] = {}
            for page in doc:
                progress("read", page_number(page) / len(doc))
                scanned = is_scanned(page)
                pages.append(
                    {
                        "width": page.rect.width,
                        "height": page.rect.height,
                        "scanned": scanned,
                    }
                )
                nl = native_layer(page)
                if nl.tokens:
                    layers.append(
                        nl
                    )  # includes hidden OCR layers of scans: they must be redacted too
                if scanned:
                    words = self.ocr.page_words(page)
                    ocr_words[page_number(page)] = words
                    layers.append(ocr_layer(page_number(page), words))
            t1 = time.perf_counter()
            progress("detect", 0.0)
            findings: list[Finding] = analyze_layers(
                layers, self.ner, self.ner_threshold, self.person_threshold
            )
            t2 = time.perf_counter()
            for page in doc:
                progress("detect", 0.6 + 0.4 * page_number(page) / len(doc))
                vis += visual.codes(page)
                if pages[page_number(page)]["scanned"]:
                    vis += visual.unexplained_ink(page, ocr_words[page_number(page)])
                    vis += visual.stamp_from_text(
                        page_number(page), ocr_words[page_number(page)]
                    )
                else:
                    vis += visual.embedded_images(page)
            t3 = time.perf_counter()

        a = Analysis(
            Path(path),
            pages,
            timings={
                "text_layers": round(t1 - t0, 2),
                "detection": round(t2 - t1, 2),
                "visual": round(t3 - t2, 2),
            },
            layers=layers,
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
    rank = {"zxing": 0, "image": 1, "stamp-text": 2, "colour": 3, "ink": 4}
    for v in sorted(vis, key=lambda v: rank.get(v.source, 9)):
        r = pymupdf.Rect(v.rect)
        if any(
            o.page == v.page
            and (pymupdf.Rect(o.rect) & r).get_area() > 0.5 * r.get_area()
            for o in out
        ):
            continue
        out.append(v)
    return out
