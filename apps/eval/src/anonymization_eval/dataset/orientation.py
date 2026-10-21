"""Edge-case document for the OCR orientation and character-box handling.

One synthetic referral in three pages, each one a case the generated documents
do not have:

- page 1, upright: a patient label pasted upside down, the codice fiscale and
  the date of birth written one character per box (a code with letters and
  digits that OCR confuses: O/0, I/1);
- page 2: fed to the scanner upside down;
- page 3: sent by fax (low resolution, black and white, streaks).

The native PDF is written with reportlab and the ground truth is taken from its
text layer (pypdfium2); the scans go through ``scan.make_scan`` like every other
document. Everything is invented.
"""

import json
import random
from pathlib import Path

import pypdfium2 as pdfium
from anonymization_core.pdf_pages import PageView
from codicefiscale import codicefiscale
from reportlab.pdfgen import canvas

from anonymization_eval.dataset import scan
from anonymization_eval.pdf import search

DOC_ID = "e056_orientamento_ocr"
SEED = 56
FONT = "Helvetica"
WIDTH, HEIGHT = 595.0, 842.0

PATIENT_LAST, PATIENT_FIRST = "Oliviero", "Ilaria"
PATIENT = f"{PATIENT_FIRST} {PATIENT_LAST}"
PATIENT_UPPER = f"{PATIENT_LAST.upper()} {PATIENT_FIRST.upper()}"
BIRTH, BIRTH_PLACE = "01/10/1971", "Bologna"
CF = codicefiscale.encode(PATIENT_LAST, PATIENT_FIRST, "F", BIRTH, BIRTH_PLACE)
DOCTOR = "Ruggero Pastorelli"
REPORTER = "Marta Ghedini"
DOCTOR_PHONE = "051 334 2210"
PATIENT_PHONE = "348 905 1176"
NOSOLOGICO = "2026045812"
EMAIL = "ilaria.oliviero71@libero.it"
CARD = "80380001200456789012"
STREET = "Via del Pratello 41"
STUDIO = "Via Gramsci 18"

# (text, label, required): every value of the document that is ground truth
MENTIONS = [
    (DOCTOR, "PERSON", True),
    (STUDIO, "ADDRESS", True),
    (DOCTOR_PHONE, "PHONE", True),
    ("03/09/2026", "DATE", False),
    (PATIENT, "PERSON", True),
    (BIRTH_PLACE, "LOCATION", True),
    (BIRTH, "DATE_OF_BIRTH", True),
    (" ".join(CF), "FISCAL_CODE", True),
    (" ".join(BIRTH.replace("/", "")), "DATE_OF_BIRTH", True),
    (PATIENT_UPPER, "PERSON", True),
    (NOSOLOGICO, "MEDICAL_RECORD", True),
    (PATIENT_PHONE, "PHONE", True),
    (CF, "FISCAL_CODE", True),
    (STREET, "ADDRESS", True),
    (EMAIL, "EMAIL", True),
    (REPORTER, "PERSON", True),
    (CARD, "HEALTH_CARD", True),
]


def _boxes(page: pdfium.PdfPage, pno: int, value: str) -> list[dict]:
    """One box per line for every occurrence of ``value``."""
    return [
        {"page": pno, "rect": [round(v, 2) for v in r], "text": value}
        for r in search(page, value)
    ]


def _put(c: canvas.Canvas, x: float, y: float, text: str, size: float = 10.5) -> None:
    """Text with its baseline starting at ``(x, y)``, top-left coordinates."""
    c.setFont(FONT, size)
    c.drawString(x, HEIGHT - y, text)


def _text(c: canvas.Canvas, y: float, text: str, size: float = 10.5) -> float:
    _put(c, 60, y, text, size)
    return y + size * 1.6


def _frame(c: canvas.Canvas, x0: float, y0: float, x1: float, y1: float, width: float):
    c.setLineWidth(width)
    c.rect(x0, HEIGHT - y1, x1 - x0, y1 - y0, stroke=1, fill=0)


def _boxed(c: canvas.Canvas, x: float, y: float, value: str, cell: float = 15) -> None:
    """``value`` written one character per box, top-left corner at (x, y)."""
    for i, ch in enumerate(value):
        _frame(c, x + i * cell, y, x + (i + 1) * cell, y + cell, 0.7)
        _put(c, x + i * cell + 4.2, y + cell - 4, ch)


def _letter(c: canvas.Canvas) -> None:
    y = _text(c, 60, f"Dott. {DOCTOR}", 14)
    y = _text(c, y, f"Medico di Medicina Generale - Studio: {STUDIO}")
    y = _text(c, y, f"Tel. {DOCTOR_PHONE}")
    y = _text(c, y + 10, "Data: 03/09/2026")
    y = _text(c, y + 6, "Oggetto: richiesta di consulenza endocrinologica", 12)
    y = _text(c, y + 4, f"Paziente: {PATIENT}, nata a {BIRTH_PLACE} il {BIRTH}")
    _put(c, 60, y + 11, "Codice fiscale")
    _boxed(c, 150, y, CF)
    y += 26
    _put(c, 60, y + 11, "Data di nascita")
    _boxed(c, 150, y, BIRTH.replace("/", ""))
    y += 34
    for line in (
        "Gentile Collega, invio la paziente per tiroidite di Hashimoto in terapia",
        "con levotiroxina 75 mcg, con TSH 6,8 mUI/L al controllo di agosto.",
        "Riferisce astenia e aumento ponderale di 4 kg negli ultimi sei mesi.",
        "Chiedo valutazione e indicazione all'aggiustamento della posologia.",
    ):
        y = _text(c, y, line)
    y = _text(c, y + 20, "Cordiali saluti")
    _text(c, y, f"Dott. {DOCTOR}")
    # patient label pasted upside down: text rotated by 180 degrees in a frame
    x0, y0, x1, y1 = 330, 640, 545, 712
    _frame(c, x0, y0, x1, y1, 0.8)
    for k, line in enumerate(
        (PATIENT_UPPER, f"Nosologico {NOSOLOGICO}", f"Tel. {PATIENT_PHONE}")
    ):
        c.saveState()
        c.translate(x1 - 10, HEIGHT - (y0 + 12 + k * 20))
        c.rotate(180)
        c.setFont(FONT, 11)
        c.drawString(0, 0, line)
        c.restoreState()


def _report(c: canvas.Canvas) -> None:
    y = _text(c, 60, "Ambulatorio di Endocrinologia - Referto di visita", 13)
    y = _text(c, y + 6, f"Paziente: {PATIENT_UPPER} - C.F. {CF}")
    y = _text(c, y, f"Residenza: {STREET}, Bologna")
    y = _text(c, y, f"Email: {EMAIL} - Cellulare: {PATIENT_PHONE}")
    y += 10
    for line in (
        "Anamnesi: tiroidite cronica autoimmune nota dal 2019, in terapia sostitutiva.",
        "Esame obiettivo: tiroide di volume lievemente aumentato, non noduli palpabili.",
        "Ecografia: ecostruttura disomogenea, assenza di formazioni nodulari.",
        "Conclusioni: si consiglia levotiroxina 100 mcg/die e controllo TSH a 8 settimane.",
    ):
        y = _text(c, y, line)
    _text(c, y + 24, f"Il medico refertante: Dott.ssa {REPORTER}")


def _fax(c: canvas.Canvas) -> None:
    y = _text(c, 60, "PROMEMORIA PRESCRIZIONE - TRASMESSA VIA FAX", 13)
    y = _text(c, y + 8, f"Assistito: {PATIENT_UPPER}", 12)
    y = _text(c, y, f"Tessera sanitaria: {CARD}", 12)
    y = _text(c, y, f"Nata il {BIRTH}", 12)
    y = _text(c, y + 6, "Prescrizione: TSH reflex, FT4, anticorpi anti-TPO", 12)
    y = _text(c, y, f"Medico prescrittore: Dott. {DOCTOR}", 12)
    _text(c, y, f"Recapito studio: {DOCTOR_PHONE}", 12)


def build(out: Path) -> list[Path]:
    """Write the document in every variant, with its ground truth, under ``out``.

    Args:
        out: Dataset folder (``native/``, ``scan/``, ``scan_ocr/``, ``ground_truth/``).

    Returns:
        The PDFs written.
    """
    native = out / "native" / f"{DOC_ID}.pdf"
    for v in ("native", "scan", "scan_ocr"):
        (out / v).mkdir(parents=True, exist_ok=True)
        (out / "ground_truth" / v).mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(native), pagesize=(WIDTH, HEIGHT), pageCompression=1)
    c.setTitle("Richiesta di consulenza")
    c.setProducer("MMG")
    for fill in (_letter, _report, _fax):
        fill(c)
        c.showPage()
    c.save()
    doc = pdfium.PdfDocument(native)
    try:
        pages_of = [doc[i] for i in range(len(doc))]
        mentions = []
        for mid, (value, label, required) in enumerate(MENTIONS, 1):
            boxes = [
                b for pno, page in enumerate(pages_of) for b in _boxes(page, pno, value)
            ]
            if not boxes:
                raise ValueError(f"mention not found in the document: {label}")
            mentions.append(
                {
                    "id": mid,
                    "text": value,
                    "label": label,
                    "required": required,
                    "visible": True,
                    "kind": "text",
                    "boxes": boxes,
                }
            )
        keep = [
            {"term": "Hashimoto", "page": pno, "rect": [round(v, 2) for v in r]}
            for pno, page in enumerate(pages_of)
            for r in search(page, "Hashimoto")
        ]
        views = [PageView(page, pno) for pno, page in enumerate(pages_of)]
        pages = [{"width": v.width, "height": v.height} for v in views]
        for page in pages_of:
            page.close()
    finally:
        doc.close()
    gt = {"pages": pages, "mentions": mentions, "keep": keep}
    meta = {
        "doc_id": DOC_ID,
        "doc_type": "orientamento_ocr",
        "title": "Richiesta di consulenza",
        "font_family": "Helvetica",
        "seed": SEED,
    }
    gt_dir = out / "ground_truth"
    (gt_dir / "native" / f"{DOC_ID}.json").write_text(
        json.dumps({**meta, "variant": "native", **gt}, ensure_ascii=False, indent=1)
    )
    written = [native]
    for variant, layer in (("scan", False), ("scan_ocr", True)):
        pdf = out / variant / f"{DOC_ID}.pdf"
        sgt = scan.make_scan(
            native,
            gt,
            pdf,
            random.Random(SEED * 31),
            ocr_layer=layer,
            turned=frozenset({1}),
            faxed=frozenset({2}),
        )
        (gt_dir / variant / f"{DOC_ID}.json").write_text(
            json.dumps(
                {**meta, "variant": variant, **sgt}, ensure_ascii=False, indent=1
            )
        )
        written.append(pdf)
    return written
