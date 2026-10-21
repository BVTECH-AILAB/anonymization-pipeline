"""Fonts, paragraph styles and page decorations (letterheads, footers, margin text)."""

import random
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.fonts import addMapping
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont

from anonymization_eval.dataset.identities import Doctor, Hospital, Person
from anonymization_eval.dataset.registry import Registry

DEJAVU = Path("/usr/share/fonts/truetype/dejavu")
_FAMILIES: dict[str, tuple[str, str, str, str]] = {
    "Helvetica": (
        "Helvetica",
        "Helvetica-Bold",
        "Helvetica-Oblique",
        "Helvetica-BoldOblique",
    ),
    "Times": ("Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"),
}


def _register_ttf(family: str, files: tuple[str, str, str, str]) -> bool:
    if not all((DEJAVU / f).exists() for f in files):
        return False
    names = (family, f"{family}-Bold", f"{family}-Italic", f"{family}-BoldItalic")
    for name, f in zip(names, files, strict=True):
        pdfmetrics.registerFont(TTFont(name, str(DEJAVU / f)))
    addMapping(family, 0, 0, names[0])
    addMapping(family, 1, 0, names[1])
    addMapping(family, 0, 1, names[2])
    addMapping(family, 1, 1, names[3])
    _FAMILIES[family] = names
    return True


_register_ttf(
    "DejaVuSans",
    (
        "DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf",
        "DejaVuSans-Oblique.ttf",
        "DejaVuSans-BoldOblique.ttf",
    ),
)
_register_ttf(
    "DejaVuSerif",
    (
        "DejaVuSerif.ttf",
        "DejaVuSerif-Bold.ttf",
        "DejaVuSerif-Italic.ttf",
        "DejaVuSerif-BoldItalic.ttf",
    ),
)


def families() -> list[str]:
    return list(_FAMILIES)


def fonts(family: str) -> tuple[str, str, str, str]:
    return _FAMILIES[family]


GREY = HexColor("#555555")
LIGHT_GREY = HexColor("#8a8a8a")


def styles(
    family: str, accent: tuple[float, float, float]
) -> dict[str, ParagraphStyle]:
    reg, bold, ital, _ = fonts(family)
    acc = Color(*accent)
    return {
        "title": ParagraphStyle(
            "title", fontName=bold, fontSize=15, leading=19, spaceAfter=8, textColor=acc
        ),
        "h": ParagraphStyle(
            "h",
            fontName=bold,
            fontSize=10.5,
            leading=14,
            spaceBefore=9,
            spaceAfter=3,
            textColor=acc,
        ),
        "body": ParagraphStyle(
            "body",
            fontName=reg,
            fontSize=9.5,
            leading=13,
            alignment=TA_JUSTIFY,
            spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "small", fontName=reg, fontSize=8, leading=10, textColor=GREY
        ),
        "cell": ParagraphStyle("cell", fontName=reg, fontSize=8.8, leading=11),
        "cellb": ParagraphStyle("cellb", fontName=bold, fontSize=8.8, leading=11),
        "italic": ParagraphStyle(
            "italic", fontName=ital, fontSize=9, leading=12, textColor=GREY
        ),
    }


@dataclass
class PageCtx:
    """Everything the per-page decorations need."""

    kind: str  # "hospital" | "mmg"
    family: str
    patient: Person
    reg: Registry
    rnd: random.Random
    hospital: Hospital | None = None
    ward: str = ""
    director: Doctor | None = None
    mmg: Doctor | None = None
    mmg_address: tuple[str, str, str] = ("", "", "")  # street, cap, city
    record_id: str = ""
    record_label: str = "Nosologico"
    printed_by: Doctor | None = None
    print_ts: str = ""
    print_id: str = ""
    hidden_trap: bool = False
    accent: tuple[float, float, float] = (0.1, 0.3, 0.5)
    band: tuple[float, float, float] = (0.1, 0.3, 0.5)
    extras: dict = field(default_factory=dict)


def draw_segments(
    c,
    x: float,
    y: float,
    segs: list[tuple[str, Color | None]],
    font: str,
    size: float,
    default: Color,
    align: str = "left",
) -> None:
    """Draw consecutive text segments, each with its own colour (None = default)."""
    total = sum(stringWidth(t, font, size) for t, _ in segs)
    if align == "right":
        x -= total
    elif align == "center":
        x -= total / 2
    c.setFont(font, size)
    for text, col in segs:
        c.setFillColor(col if col is not None else default)
        c.drawString(x, y, text)
        x += stringWidth(text, font, size)


def decorate(c, doc, ctx: PageCtx) -> None:
    W, H = doc.pagesize
    reg, bold, ital, _ = fonts(ctx.family)
    R = ctx.reg
    p = ctx.patient
    c.saveState()
    if ctx.kind == "hospital":
        h = ctx.hospital
        c.setFillColor(Color(*ctx.band))
        c.rect(0, H - 80, W, 80, stroke=0, fill=1)
        # vector logo
        c.setFillColor(white)
        c.circle(46, H - 40, 21, stroke=0, fill=1)
        c.setFillColor(Color(*ctx.band))
        c.rect(42, H - 53, 8, 26, stroke=0, fill=1)
        c.rect(33, H - 44, 26, 8, stroke=0, fill=1)
        draw_segments(
            c,
            78,
            H - 30,
            [(h.name, R.color(h.name, "ORGANIZATION", "white"))],
            bold,
            13,
            white,
        )
        draw_segments(
            c,
            78,
            H - 44,
            [
                ("Sistema Socio Sanitario Regionale — ", None),
                (h.asst, R.color(h.asst, "ORGANIZATION", "white")),
            ],
            reg,
            8.5,
            white,
        )
        segs = [(f"U.O.C. {ctx.ward}", None)]
        if ctx.director:
            segs += [
                ("  ·  Direttore: ", None),
                (ctx.director.full, R.color(ctx.director.full, "PERSON", "white")),
            ]
        draw_segments(c, 78, H - 57, segs, reg, 8.5, white)
        right = W - 28
        draw_segments(
            c,
            right,
            H - 28,
            [(h.address, R.color(h.address, "ADDRESS", "white", required=False))],
            reg,
            7.5,
            white,
            "right",
        )
        draw_segments(
            c,
            right,
            H - 39,
            [
                (f"{h.cap} ", None),
                (h.city, R.color(h.city, "LOCATION", "white", required=False)),
                (f" ({h.prov})", None),
            ],
            reg,
            7.5,
            white,
            "right",
        )
        draw_segments(
            c,
            right,
            H - 50,
            [
                ("Tel. ", None),
                (h.phone, R.color(h.phone, "PHONE", "white", required=False)),
                ("  Fax ", None),
                (h.fax, R.color(h.fax, "PHONE", "white", required=False)),
            ],
            reg,
            7.5,
            white,
            "right",
        )
        draw_segments(c, right, H - 61, [(h.web, None)], reg, 7.5, white, "right")
        c.setFillColor(Color(*ctx.accent))
        c.rect(0, H - 84, W, 4, stroke=0, fill=1)
    else:  # MMG letterhead
        d = ctx.mmg
        street, cap, city = ctx.mmg_address
        black = HexColor("#000000")
        draw_segments(
            c, 40, H - 48, [(d.full, R.color(d.full, "PERSON"))], bold, 15, black
        )
        draw_segments(
            c,
            40,
            H - 62,
            [(f"Medico di Medicina Generale · Specialista in {d.specialty}", None)],
            ital,
            9,
            GREY,
        )
        draw_segments(
            c,
            40,
            H - 76,
            [
                ("Studio: ", None),
                (street, R.color(street, "ADDRESS")),
                (" – ", None),
                (cap, R.color(cap, "ADDRESS")),
                (" ", None),
                (city, R.color(city, "LOCATION")),
            ],
            reg,
            8.5,
            GREY,
        )
        draw_segments(
            c,
            40,
            H - 88,
            [
                ("Tel. ", None),
                (d.phone, R.color(d.phone, "PHONE")),
                ("  ·  PEC ", None),
                (d.email, R.color(d.email, "EMAIL")),
            ],
            reg,
            8.5,
            GREY,
        )
        draw_segments(
            c,
            W - 40,
            H - 48,
            [
                ("Cod. regionale ", None),
                (
                    ctx.extras["mmg_code"],
                    R.color(ctx.extras["mmg_code"], "PROFESSIONAL_ID"),
                ),
            ],
            reg,
            8,
            GREY,
            "right",
        )
        draw_segments(
            c,
            W - 40,
            H - 60,
            [(d.omceo, R.color(d.omceo, "PROFESSIONAL_ID"))],
            reg,
            8,
            GREY,
            "right",
        )
        c.setStrokeColor(Color(*ctx.accent))
        c.setLineWidth(2)
        c.line(40, H - 98, W - 40, H - 98)

    # footer, repeated with patient identity on every page
    c.setStrokeColor(LIGHT_GREY)
    c.setLineWidth(0.5)
    c.line(40, 46, W - 40, 46)
    up = p.full_rev_upper
    segs = [("Paziente: ", None), (up, R.color(up, "PERSON"))]
    if ctx.record_id:
        segs += [
            (f"  ·  {ctx.record_label} ", None),
            (ctx.record_id, R.color(ctx.record_id, "MEDICAL_RECORD")),
        ]
    draw_segments(c, 40, 35, segs, reg, 7.5, GREY)
    draw_segments(c, W - 40, 35, [(f"Pag. {doc.page}", None)], reg, 7.5, GREY, "right")
    draw_segments(
        c,
        40,
        25,
        [
            (
                "Documento riservato contenente dati sanitari (Reg. UE 2016/679). "
                "Copia conforme all'originale informatico.",
                None,
            )
        ],
        ital,
        6.5,
        LIGHT_GREY,
    )

    # vertical print trace in the left margin
    if ctx.printed_by:
        c.saveState()
        c.translate(20, 120)
        c.rotate(90)
        u = ctx.printed_by.name
        draw_segments(
            c,
            0,
            0,
            [
                ("Stampato da ", None),
                (u, R.color(u, "PERSON")),
                (f" il {ctx.print_ts} — ID stampa ", None),
                (ctx.print_id, R.color(ctx.print_id, "MEDICAL_RECORD")),
            ],
            reg,
            6.5,
            LIGHT_GREY,
        )
        c.restoreState()

    # invisible trap: white 2pt text, as left by some HIS exports
    if ctx.hidden_trap:
        draw_segments(
            c,
            300,
            12,
            [
                ("REF ", None),
                (p.cf, R.color(p.cf, "FISCAL_CODE", "white", visible=False)),
                (" ", None),
                (up, R.color(up, "PERSON", "white", visible=False)),
            ],
            reg,
            2,
            white,
        )
    c.restoreState()
