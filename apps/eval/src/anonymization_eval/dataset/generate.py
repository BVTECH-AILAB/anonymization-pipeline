"""Render synthetic documents, add file-level traps and extract ground truth."""

import json
import random
from pathlib import Path

import pypdfium2 as pdfium
from anonymization_core.geometry import intersects
from anonymization_core.pdf_pages import PageView
from pypdf import PdfWriter
from pypdf.annotations import Text
from pypdf.generic import NameObject, TextStringObject
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

from anonymization_eval.dataset import scan
from anonymization_eval.dataset.docs import BUILDERS, Built
from anonymization_eval.dataset.identities import IdentityFactory
from anonymization_eval.dataset.layout import decorate
from anonymization_eval.dataset.registry import Registry, rgb_to_id
from anonymization_eval.pdf import color_spans, images, search, words

# Terms that look like names but must survive anonymization.
KEEP_TERMS = [
    "Parkinson",
    "Crohn",
    "Hashimoto",
    "Cushing",
    "Babinski",
    "Murphy",
    "Blumberg",
    "Giordano",
    "Riva-Rocci",
    "Lasègue",
    "Romberg",
    "Kernig",
    "Brudzinski",
    "Glasgow",
    "Rankin",
    "Fleischner",
    "Fazekas",
    "Willis",
    "Wirsung",
    "Pacini",
    "Morgagni",
    "Santorini",
    "Corti",
    "Golgi",
    "Malpighi",
    "Forlanini",
    "Ruffini",
    "NYHA",
    "CKD-EPI",
]


def render(built: Built, reg: Registry, path: Path) -> None:
    md = built.metadata
    if built.canvas_fn:
        c = canvas.Canvas(str(path), pagesize=A4)
        c.setTitle(md["title"])
        c.setAuthor(md["author"])
        c.setSubject(md["subject"])
        c.setKeywords(md["keywords"])
        built.canvas_fn(c, reg)
        c.showPage()
        c.save()
        return
    ctx = built.ctx
    if ctx is None:
        raise ValueError("a story document needs a page context")
    top = 100 if ctx.kind == "hospital" else 115
    left, bottom = 42, 62
    doc = BaseDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=left,
        rightMargin=42,
        topMargin=top,
        bottomMargin=bottom,
        title=md["title"],
        author=md["author"],
        subject=md["subject"],
        keywords=md["keywords"],
        creator="HIS Report Engine 4.2",
    )
    frame = Frame(left, bottom, doc.width, doc.height, id="body")
    doc.addPageTemplates(
        [PageTemplate(id="p", frames=[frame], onPage=lambda c, d: decorate(c, d, ctx))]
    )
    doc.build(built.story)


def add_annotation_history(path: Path, reg: Registry, built: Built) -> None:
    """A sticky note with PII is added then deleted via incremental saves:
    invisible on screen, still present in the file bytes."""
    p = built.ctx.patient if built.ctx else None
    if p is None:
        return
    note = f"Richiamare {p.last} al {p.phone} per ritiro"
    writer = PdfWriter(path, incremental=True)
    top = float(writer.pages[0].mediabox.top)
    annot = Text(rect=(500, top - 140, 520, top - 120), text=note)
    annot[NameObject("/T")] = TextStringObject("operatore")
    writer.add_annotation(page_number=0, annotation=annot)
    with path.open("wb") as fh:
        writer.write(fh)
    writer = PdfWriter(path, incremental=True)
    del writer.pages[0][NameObject("/Annots")]
    with path.open("wb") as fh:
        writer.write(fh)
    reg.hidden(p.last, "PERSON", "annotation")
    reg.hidden(p.phone, "PHONE", "annotation")


def extract_ground_truth(path: Path, reg: Registry) -> dict:
    pages, keep = [], []
    doc = pdfium.PdfDocument(path)
    try:
        for pno in range(len(doc)):
            page = doc[pno]
            view = PageView(page, pno)
            pages.append({"width": view.width, "height": view.height})
            for rect, text, rgb in color_spans(page):
                mid = rgb_to_id(rgb)
                if not mid or not text.strip():
                    continue
                m = reg.by_id(mid)
                if m is None:
                    raise ValueError(f"unknown mention id {mid} in {path}")
                m.boxes.append(
                    {"page": pno, "rect": [round(v, 2) for v in rect], "text": text}
                )
            for rect, width, height in images(page):
                mid = reg.image_id(width, height)
                if mid:
                    m = reg.by_id(mid)
                    if m is None:
                        raise ValueError(f"unknown image id {mid} in {path}")
                    m.boxes.append({"page": pno, "rect": [round(v, 2) for v in rect]})
            pii_rects = [
                tuple(b["rect"])
                for m in reg.mentions
                for b in m.boxes
                if b["page"] == pno
            ]
            for term in KEEP_TERMS:
                for r in search(page, term):
                    if not any(intersects(r, pr) for pr in pii_rects):
                        keep.append(
                            {
                                "term": term,
                                "page": pno,
                                "rect": [round(v, 2) for v in r],
                            }
                        )
            page.close()
    finally:
        doc.close()
    missing = [
        m.text for m in reg.mentions if m.kind in ("text", "image") and not m.boxes
    ]
    if missing:
        raise ValueError(f"mentions without boxes in {path}: {missing[:5]}")
    return {
        "pages": pages,
        "mentions": [m.to_dict() for m in reg.mentions],
        "keep": keep,
    }


def _layout(path: Path) -> list[list[tuple[str, tuple[float, ...]]]]:
    """Per page: every word and every image (by pixel size) with its box."""
    doc = pdfium.PdfDocument(path)
    try:
        return [
            [(text, rect) for rect, text in words(doc[i])]
            + sorted((f"image {w}x{h}", rect) for rect, w, h in images(doc[i]))
            for i in range(len(doc))
        ]
    finally:
        doc.close()


def same_layout(a: Path, b: Path, tol: float = 0.05) -> bool:
    """True when ``a`` and ``b`` place the same words and images at the same
    boxes, within ``tol`` points (pdfium positions are single precision)."""
    la, lb = _layout(a), _layout(b)
    return len(la) == len(lb) and all(
        len(pa) == len(pb)
        and all(
            ta == tb and all(abs(x - y) <= tol for x, y in zip(ra, rb, strict=True))
            for (ta, ra), (tb, rb) in zip(pa, pb, strict=True)
        )
        for pa, pb in zip(la, lb, strict=True)
    )


def remove_documents(out: Path, pattern: str) -> None:
    """Delete every file of the documents whose id matches the glob ``pattern``.

    The dataset folder also holds documents this generator cannot rebuild
    (from other sources and hand-annotated): ``--overwrite`` removes only ``NNN_*``.
    """
    for v in ("native", "scan", "scan_ocr"):
        for f in [
            *(out / v).glob(f"{pattern}.pdf"),
            *(out / "ground_truth" / v).glob(f"{pattern}.json"),
        ]:
            f.unlink()


def next_index(out: Path) -> int:
    """First free document number in an existing dataset folder (0 if empty)."""
    nums = [
        int(f.name.split("_", 1)[0])
        for f in (out / "native").glob("*.pdf")
        if not f.name.startswith(".") and f.name.split("_", 1)[0].isdigit()
    ]
    return max(nums) + 1 if nums else 0


def _build(dtype: str, seed: int, i: int, clean: bool) -> tuple[Registry, Built]:
    """Build document ``i``; the same seed gives the same document, encoded or clean."""
    reg = Registry(clean=clean)
    built = BUILDERS[dtype](
        IdentityFactory(seed * 7919 + i), reg, random.Random(seed * 100003 + i)
    )
    return reg, built


def generate(
    n: int,
    out: Path,
    seed: int = 7,
    scans: bool = True,
    start: int = 0,
) -> list[Path]:
    """Writes input PDFs to out/<variant>/ and ground truth to out/ground_truth/<variant>/.

    Each document is rendered twice from the same seed: an encoded copy (colour-coded
    mentions, used only to extract ground truth, then deleted) and a clean copy with
    pure colours, which is what goes into the dataset. Their layouts must be identical.
    """
    types = list(BUILDERS)
    variants = ("native", "scan", "scan_ocr") if scans else ("native",)
    for v in variants:
        (out / v).mkdir(parents=True, exist_ok=True)
        (out / "ground_truth" / v).mkdir(parents=True, exist_ok=True)
    written = []
    for i in range(start, start + n):
        dtype = types[i % len(types)]
        doc_id = f"{i:03d}_{dtype}"
        pdf = out / "native" / f"{doc_id}.pdf"
        encoded = out / "native" / f".{doc_id}.encoded.pdf"

        reg, built = _build(dtype, seed, i, clean=False)
        render(built, reg, encoded)
        clean_reg, clean_built = _build(dtype, seed, i, clean=True)
        render(clean_built, clean_reg, pdf)
        if not same_layout(encoded, pdf):
            raise RuntimeError(f"clean and encoded renders differ for {doc_id}")
        if built.ctx and random.Random(seed * 13 + i).random() < 0.35:
            add_annotation_history(pdf, reg, clean_built)
        gt = extract_ground_truth(encoded, reg)
        encoded.unlink()
        meta = {
            "doc_id": doc_id,
            "doc_type": built.doc_type,
            "title": built.title,
            "font_family": built.family,
            "seed": seed,
        }
        gt_dir = out / "ground_truth"
        (gt_dir / "native" / f"{doc_id}.json").write_text(
            json.dumps(
                {**meta, "variant": "native", **gt}, ensure_ascii=False, indent=1
            )
        )
        written.append(pdf)
        if scans:
            for variant, ocr_layer in (("scan", False), ("scan_ocr", True)):
                spdf = out / variant / f"{doc_id}.pdf"
                sgt = scan.make_scan(
                    pdf, gt, spdf, random.Random(seed * 31 + i), ocr_layer=ocr_layer
                )
                (gt_dir / variant / f"{doc_id}.json").write_text(
                    json.dumps(
                        {**meta, "variant": variant, **sgt},
                        ensure_ascii=False,
                        indent=1,
                    )
                )
                written.append(spdf)
    return written
