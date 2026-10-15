"""Fail-closed verification of a redacted PDF.

1. under every box no original text survives (only our tag)
2. no redacted value appears anywhere in the output text, raw (decompressed)
   bytes or metadata
3. preservation: rendered pages are identical outside the boxes
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf

from anonmyzation_pipeline.services.anonymization.pipeline import Analysis
from anonmyzation_pipeline.services.anonymization.taxonomy import LABELS

# values strong enough to search globally (single surnames can collide with eponyms)
GLOBAL = {
    "FISCAL_CODE",
    "HEALTH_CARD",
    "PHONE",
    "EMAIL",
    "IBAN",
    "URL",
    "MEDICAL_RECORD",
    "ID_DOCUMENT",
    "PRESCRIPTION_ID",
    "PROFESSIONAL_ID",
    "DATE_OF_BIRTH",
}
_TAGS = {w for lab in LABELS.values() for t in (lab.tag, lab.short) for w in t.split()}


@dataclass
class Report:
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    pixel_changed_outside: list[float] = field(
        default_factory=list
    )  # per page, fraction of pixels

    def fail(self, msg: str):
        self.ok = False
        self.issues.append(msg)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def verify(
    src: Path, out: Path, analysis: Analysis, dpi: int = 72, tol: float = 2e-4
) -> Report:
    rep = Report()
    a, b = pymupdf.open(src), pymupdf.open(out)
    boxes = [x for x in analysis.boxes if x.redact]

    # 1. nothing left under the boxes
    for x in boxes:
        if x.layer == "visual":
            continue
        r = pymupdf.Rect(x.rect)
        r = pymupdf.Rect(
            r.x0 + 1, r.y0 + r.height * 0.3, r.x1 - 1, r.y1 - r.height * 0.3
        )
        left = [
            w
            for w in b[x.page].get_text("words", clip=r)
            if w[4] not in _TAGS and pymupdf.Rect(w[:4]).intersects(r)
        ]
        orig = _norm(x.text)
        for w in left:
            if len(w[4]) >= 2 and _norm(w[4]) in orig:
                rep.fail(f"p{x.page + 1}: '{w[4]}' still under {x.label} box")

    # 2. global search for strong values
    full = _norm(" ".join(p.get_text() for p in b))
    raw = b"".join(
        (b.xref_stream(i) or b"") + b.xref_object(i).encode("latin-1", "ignore")
        for i in range(1, b.xref_length())
    )
    raw_low = raw.lower()
    md = " ".join(str(v) for v in (b.metadata or {}).values() if v) + (
        b.get_xml_metadata() or ""
    )
    values = {x.text for x in boxes if x.label in GLOBAL and len(_norm(x.text)) >= 6}
    values |= {
        x.text for x in boxes if x.label == "PERSON" and len(x.text.split()) >= 2
    }
    # an occurrence the user chose to keep visible is not a leak
    values -= {
        x.text
        for x in analysis.boxes
        if not x.redact and "removed-by-user" in x.sources
    }
    for v in values:
        nv = _norm(v)
        if nv in full:
            rep.fail(f"value still in text: '{v}'")
        if v.lower().encode("latin-1", "ignore") in raw_low and len(v) >= 6:
            rep.fail(f"value still in file bytes: '{v}'")
        if nv and nv in _norm(md):
            rep.fail(f"value still in metadata: '{v}'")
    for pno in range(b.page_count):
        if list(b[pno].annots() or []):
            rep.fail(f"p{pno + 1}: annotations left")

    # 3. preservation outside the boxes
    for pno in range(min(a.page_count, b.page_count)):
        pa, pb = a[pno], b[pno]
        ia = pa.get_pixmap(dpi=dpi, alpha=False, annots=False)
        ib = pb.get_pixmap(dpi=dpi, alpha=False, annots=False)
        A = (
            np.frombuffer(ia.samples, np.uint8)
            .reshape(ia.height, ia.width, ia.n)
            .astype(np.int16)
        )
        B = (
            np.frombuffer(ib.samples, np.uint8)
            .reshape(ib.height, ib.width, ib.n)
            .astype(np.int16)
        )
        diff = np.abs(A - B).max(axis=2) > 40
        s = dpi / 72
        rects = [r for pno, r, _ in getattr(analysis, "applied", []) if pno == pno] or [
            x.rect for x in boxes if x.page == pno
        ]
        for x0, y0, x1, y1 in rects:
            diff[
                max(0, int((y0 - 3) * s)) : int((y1 + 3) * s) + 1,
                max(0, int((x0 - 3) * s)) : int((x1 + 3) * s) + 1,
            ] = False
        frac = float(diff.mean())
        rep.pixel_changed_outside.append(frac)
        if frac > tol:
            rep.fail(f"p{pno + 1}: {frac:.2%} of pixels changed outside the boxes")
    return rep
