"""BV TECH technical document of anonymization-pipeline (A4, Italian, max 10 pages).

Run from the repository root:

    uv run --no-project --with reportlab python docs/pdf/build_pdf.py

It writes docs/anonymization-pipeline.pdf (or the path given as first argument).
Fonts: Arial (brand font) from the Windows fonts folder or the system; otherwise
Liberation Sans or DejaVu Sans. Brand assets are in docs/pdf/assets/.
"""

import math
import sys
from pathlib import Path

from PIL import Image
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)

HERE = Path(__file__).parent
ASSETS = HERE / "assets"
OUT = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else HERE.parent / "anonymization-pipeline.pdf"
)

# (regular, bold, italic, bold italic): the first family found is used
FONT_FAMILIES = [
    ("/mnt/c/Windows/Fonts", ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf")),
    (
        "/usr/share/fonts/truetype/msttcorefonts",
        ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    ),
    (
        "/usr/share/fonts/truetype/liberation",
        (
            "LiberationSans-Regular.ttf",
            "LiberationSans-Bold.ttf",
            "LiberationSans-Italic.ttf",
            "LiberationSans-BoldItalic.ttf",
        ),
    ),
    (
        "/usr/share/fonts/truetype/dejavu",
        (
            "DejaVuSans.ttf",
            "DejaVuSans-Bold.ttf",
            "DejaVuSans-Oblique.ttf",
            "DejaVuSans-BoldOblique.ttf",
        ),
    ),
]
MONO_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/mnt/c/Windows/Fonts/consola.ttf",
]


def register_fonts() -> None:
    """Register the body family as "Arial" and a monospace font as "Mono"."""
    for folder, files in FONT_FAMILIES:
        paths = [Path(folder) / f for f in files]
        if all(p.exists() for p in paths):
            for name, path in zip(
                ("Arial", "Arial-Bold", "Arial-Italic", "Arial-BoldItalic"),
                paths,
                strict=True,
            ):
                pdfmetrics.registerFont(TTFont(name, str(path)))
            break
    else:
        raise SystemExit("No Arial, Liberation Sans or DejaVu Sans font found")
    pdfmetrics.registerFontFamily(
        "Arial",
        normal="Arial",
        bold="Arial-Bold",
        italic="Arial-Italic",
        boldItalic="Arial-BoldItalic",
    )
    mono = next((p for p in MONO_FONTS if Path(p).exists()), None)
    if mono is None:
        raise SystemExit("No monospace font found")
    pdfmetrics.registerFont(TTFont("Mono", mono))


register_fonts()

# bottom 40% of the brand cover background: the BV TECH watermark wordmark
_cover = Image.open(ASSETS / "cover-bg-01.png")
COVER_STRIP = _cover.crop((0, int(_cover.height * 0.60), _cover.width, _cover.height))

DEEP = colors.HexColor("#005E73")
DARK = colors.HexColor("#003E51")
MEDIUM = colors.HexColor("#00808E")
LIGHT = colors.HexColor("#009CA6")
CYAN = colors.HexColor("#33B0B8")
GREEN = colors.HexColor("#00A17B")
MINT = colors.HexColor("#70C0A6")
MUTED = colors.HexColor("#3A6973")
TINT = colors.HexColor("#E8F3F5")
RULE = colors.HexColor("#B9D5DB")

W, H = A4
ML = MR = 18 * mm
MT = 24 * mm
MB = 22 * mm

FOOTER = "ANONIMIZZAZIONE DOCUMENTI CLINICI"
CONFIDENTIAL = (
    "Confidenziale. Questo documento contiene informazioni riservate e non può essere "
    "riprodotto o diffuso senza autorizzazione."
)

# --- styles -----------------------------------------------------------------

body = ParagraphStyle(
    "body",
    fontName="Arial",
    fontSize=9.4,
    leading=13.2,
    spaceAfter=5,
    alignment=TA_LEFT,
)
small = ParagraphStyle("small", parent=body, fontSize=8, leading=10.6, spaceAfter=0)
cell = ParagraphStyle("cell", parent=body, fontSize=8, leading=10.2, spaceAfter=0)
cellb = ParagraphStyle("cellb", parent=cell, fontName="Arial-Bold")
head = ParagraphStyle(
    "head", parent=cell, fontName="Arial-Bold", textColor=colors.white
)
h1 = ParagraphStyle(
    "h1",
    fontName="Arial-Bold",
    fontSize=17,
    leading=19,
    textColor=DEEP,
    spaceBefore=2,
    spaceAfter=8,
)
h2 = ParagraphStyle(
    "h2",
    fontName="Arial-Bold",
    fontSize=11,
    leading=14,
    textColor=DEEP,
    spaceBefore=8,
    spaceAfter=4,
)
bullet = ParagraphStyle(
    "bullet",
    parent=body,
    leftIndent=11,
    bulletIndent=1,
    spaceAfter=2.5,
    bulletFontName="Arial-Bold",
    bulletFontSize=7,
    bulletColor=CYAN,
)
note = ParagraphStyle("note", parent=body, fontSize=8.4, leading=11.4, textColor=MUTED)
code = ParagraphStyle("code", fontName="Mono", fontSize=7.6, leading=10, textColor=DARK)
toc = ParagraphStyle("toc", parent=body, fontSize=10.5, leading=17, spaceAfter=0)


def P(text: str, style: ParagraphStyle = body) -> Paragraph:
    return Paragraph(text, style)


def H1(num: str, text: str) -> Paragraph:
    return Paragraph(
        f'<font color="#33B0B8">{num}</font>&nbsp;&nbsp;{text.upper()}', h1
    )


def H2(text: str) -> Paragraph:
    return Paragraph(text, h2)


def bullets(items: list[str]) -> list[Paragraph]:
    return [Paragraph(i, bullet, bulletText="■") for i in items]


def table(
    rows: list[list], widths: list[float], header: bool = True, bold_first: bool = False
) -> Table:
    data = []
    for r, row in enumerate(rows):
        out = []
        for c, v in enumerate(row):
            if isinstance(v, str):
                st = (
                    head
                    if header and r == 0
                    else (cellb if bold_first and c == 0 else cell)
                )
                out.append(Paragraph(v, st))
            else:
                out.append(v)
        data.append(out)
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
    ]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), DEEP)]
        st += [("BACKGROUND", (0, i), (-1, i), TINT) for i in range(2, len(rows), 2)]
    else:
        st += [("BACKGROUND", (0, i), (-1, i), TINT) for i in range(1, len(rows), 2)]
    t.setStyle(TableStyle(st))
    return t


def callout(text: str) -> Table:
    t = Table(
        [
            [
                Paragraph(
                    text,
                    ParagraphStyle(
                        "co", parent=body, fontSize=8.8, leading=12.4, spaceAfter=0
                    ),
                )
            ]
        ],
        colWidths=[W - ML - MR],
    )
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), TINT),
                ("LINEBEFORE", (0, 0), (0, -1), 3, CYAN),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return t


# --- diagrams ---------------------------------------------------------------


def box(
    d: Drawing,
    x,
    y,
    w,
    h,
    title,
    lines=(),
    fill=DEEP,
    fg=colors.white,
    size=8.2,
    title_size=9,
):
    d.add(Rect(x, y, w, h, rx=4, ry=4, fillColor=fill, strokeColor=None))
    n = len(lines)
    total = title_size + n * (size + 2.4)
    ty = y + h / 2 + total / 2 - title_size + 1
    d.add(
        String(
            x + w / 2,
            ty,
            title,
            fontName="Arial-Bold",
            fontSize=title_size,
            fillColor=fg,
            textAnchor="middle",
        )
    )
    for i, ln in enumerate(lines):
        d.add(
            String(
                x + w / 2,
                ty - (i + 1) * (size + 2.4) - 1,
                ln,
                fontName="Arial",
                fontSize=size,
                fillColor=fg,
                textAnchor="middle",
            )
        )


def arrow(d: Drawing, x1, y1, x2, y2, color=MUTED, label=None, dash=None, label_dx=4):
    d.add(
        Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=1.1, strokeDashArray=dash)
    )
    a = math.atan2(y2 - y1, x2 - x1)
    s = 5.5
    p1 = (x2 - s * math.cos(a - 0.4), y2 - s * math.sin(a - 0.4))
    p2 = (x2 - s * math.cos(a + 0.4), y2 - s * math.sin(a + 0.4))
    d.add(Polygon([x2, y2, *p1, *p2], fillColor=color, strokeColor=None))
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        d.add(
            String(
                mx + label_dx,
                my + 3,
                label,
                fontName="Arial",
                fontSize=7.2,
                fillColor=MUTED,
            )
        )


def architecture() -> Drawing:
    w = W - ML - MR
    d = Drawing(w, 232)
    # cluster frame
    d.add(
        Rect(
            0,
            0,
            w,
            206,
            rx=6,
            ry=6,
            fillColor=None,
            strokeColor=RULE,
            strokeWidth=1,
            strokeDashArray=[3, 2],
        )
    )
    d.add(
        String(
            8,
            194,
            "Cluster Kubernetes (OKE) - namespace del servizio",
            fontName="Arial-Bold",
            fontSize=7.8,
            fillColor=MUTED,
        )
    )
    # client
    box(
        d,
        w / 2 - 110,
        212,
        220,
        20,
        "Client: fe / WSO2 API Manager",
        fill=MUTED,
        title_size=8.4,
    )
    arrow(d, w / 2, 212, w / 2, 178, label="HTTPS  /anonymizations")
    # backend pod
    bx, by, bw, bh = w / 2 - 125, 92, 250, 86
    d.add(
        Rect(
            bx,
            by,
            bw,
            bh,
            rx=5,
            ry=5,
            fillColor=TINT,
            strokeColor=DEEP,
            strokeWidth=1.2,
        )
    )
    d.add(
        String(
            bx + 8,
            by + bh - 13,
            "Pod 1  -  anonymization-pipeline  (:8000)",
            fontName="Arial-Bold",
            fontSize=8.6,
            fillColor=DEEP,
        )
    )
    box(
        d,
        bx + 8,
        by + 26,
        112,
        36,
        "API  (src/router)",
        ["sessioni, stream SSE, KPI"],
        fill=DEEP,
    )
    box(
        d,
        bx + 130,
        by + 26,
        112,
        36,
        "Motore",
        ["src/services/anonymizer"],
        fill=MEDIUM,
    )
    arrow(d, bx + 120, by + 44, bx + 130, by + 44, color=DEEP)
    d.add(
        String(
            bx + 8,
            by + 9,
            "nessuna GPU  -  emptyDir per i documenti (TTL)",
            fontName="Arial",
            fontSize=7.4,
            fillColor=MUTED,
        )
    )
    # ocr pod
    ox, oy, ow, oh = 6, 10, 200, 62
    d.add(
        Rect(
            ox,
            oy,
            ow,
            oh,
            rx=5,
            ry=5,
            fillColor=TINT,
            strokeColor=LIGHT,
            strokeWidth=1.2,
        )
    )
    d.add(
        String(
            ox + 8,
            oy + oh - 13,
            "Pod 2  -  paddle-ocr-server  (:8829)",
            fontName="Arial-Bold",
            fontSize=8.6,
            fillColor=DEEP,
        )
    )
    d.add(
        String(
            ox + 8,
            oy + 33,
            "PP-OCRv5 su RapidOCR (ONNX Runtime)",
            fontName="Arial",
            fontSize=7.6,
            fillColor=DARK,
        )
    )
    d.add(
        String(
            ox + 8,
            oy + 21,
            "GPU opzionale, fallback su CPU",
            fontName="Arial",
            fontSize=7.6,
            fillColor=DARK,
        )
    )
    d.add(
        String(
            ox + 8,
            oy + 9,
            "volume /models/rapidocr (circa 90 MB)",
            fontName="Arial",
            fontSize=7.6,
            fillColor=DARK,
        )
    )
    # ner
    nx, ny, nw, nh = w - 206, 10, 200, 62
    d.add(
        Rect(
            nx,
            ny,
            nw,
            nh,
            rx=5,
            ry=5,
            fillColor=colors.white,
            strokeColor=GREEN,
            strokeWidth=1.2,
        )
    )
    d.add(
        String(
            nx + 8,
            ny + nh - 13,
            "Servizio NER AILAB (esterno)",
            fontName="Arial-Bold",
            fontSize=8.6,
            fillColor=DEEP,
        )
    )
    d.add(
        String(
            nx + 8,
            ny + 33,
            "POST /batch_ner, GET /health",
            fontName="Arial",
            fontSize=7.6,
            fillColor=DARK,
        )
    )
    d.add(
        String(
            nx + 8,
            ny + 21,
            "persone, luoghi, indirizzi, organizzazioni",
            fontName="Arial",
            fontSize=7.6,
            fillColor=DARK,
        )
    )
    d.add(
        String(
            nx + 8,
            ny + 9,
            "fail-closed: se non risponde il job fallisce",
            fontName="Arial",
            fontSize=7.6,
            fillColor=DARK,
        )
    )
    arrow(
        d,
        bx + 60,
        by,
        ox + 120,
        oy + oh,
        color=LIGHT,
        label="POST /ocr, /recognize",
        label_dx=-92,
    )
    arrow(
        d,
        bx + bw - 60,
        by,
        nx + 80,
        ny + nh,
        color=GREEN,
        label="testo di pagina",
        label_dx=6,
    )
    return d


def pipeline() -> Drawing:
    w = W - ML - MR
    d = Drawing(w, 150)
    bw, bh, gap = (w - 4 * 12) / 5, 46, 12
    row1 = [
        ("1. Upload", ["PDF nativo, scansione,", "JPG / PNG / TIFF"], MUTED),
        ("2. Conversione", ["tutto diventa PDF;", "rotazione EXIF"], DEEP),
        ("3. Layer di testo", ["nativo: pypdfium2", "scansione: server OCR"], DEEP),
        ("4. Rilevamento", ["testo: regole, NER", "pixel: firme, timbri"], MEDIUM),
        ("5. Box", ["una per riga;", "policy per etichetta"], MEDIUM),
    ]
    row2 = [
        ("6. Revisione", ["l'utente aggiunge o", "toglie box (accurate)"], LIGHT),
        ("7. Redazione", ["pagine con box:", "immagine + testo invisibile"], DEEP),
        ("8. Verifica", ["fail-closed:", "testo, byte, metadati, pixel"], DARK),
        ("9a. Rilascio", ["PDF + Word (accurate)", "Markdown (fast)"], GREEN),
        (
            "9b. Blocco",
            ["non scaricabile,", "motivi mostrati"],
            colors.HexColor("#8A2B2B"),
        ),
    ]
    y1, y2 = 96, 20
    for i, (t, ls, c) in enumerate(row1):
        x = i * (bw + gap)
        box(d, x, y1, bw, bh, t, ls, fill=c, size=7.2, title_size=8.4)
        if i:
            arrow(d, x - gap, y1 + bh / 2, x, y1 + bh / 2)
    for i, (t, ls, c) in enumerate(row2):
        x = i * (bw + gap)
        box(d, x, y2, bw, bh, t, ls, fill=c, size=7.2, title_size=8.4)
        if 0 < i < 4:
            arrow(d, x - gap, y2 + bh / 2, x, y2 + bh / 2)
    # 5 -> 6 return line
    x5 = 4 * (bw + gap) + bw / 2
    d.add(Line(x5, y1, x5, y1 - 12, strokeColor=MUTED, strokeWidth=1.1))
    d.add(Line(x5, y1 - 12, bw / 2, y1 - 12, strokeColor=MUTED, strokeWidth=1.1))
    arrow(d, bw / 2, y1 - 12, bw / 2, y2 + bh)
    # 8 -> 9b
    x8 = 2 * (bw + gap) + bw
    d.add(Line(x8 - 20, y2, x8 - 20, y2 - 10, strokeColor=MUTED, strokeWidth=1.1))
    d.add(
        Line(
            x8 - 20,
            y2 - 10,
            4 * (bw + gap) + bw / 2,
            y2 - 10,
            strokeColor=MUTED,
            strokeWidth=1.1,
        )
    )
    arrow(d, 4 * (bw + gap) + bw / 2, y2 - 10, 4 * (bw + gap) + bw / 2, y2)
    d.add(
        String(
            x8 + 6,
            y2 - 8,
            "seconda verifica fallita",
            fontName="Arial",
            fontSize=6.8,
            fillColor=MUTED,
        )
    )
    return d


# --- page decorations ------------------------------------------------------


def on_cover(c, doc):
    c.saveState()
    c.setFillColor(DEEP)
    c.rect(0, 0, W, H, stroke=0, fill=1)
    c.drawImage(
        ImageReader(COVER_STRIP), 0, 0, W, W * COVER_STRIP.height / COVER_STRIP.width
    )
    c.drawImage(
        str(ASSETS / "bvtech-logo-payoff-white.png"),
        ML,
        H - 30 * mm,
        46 * mm,
        23 * mm,
        mask="auto",
    )
    c.setFillColor(CYAN)
    c.setFont("Arial-Bold", 10)
    c.drawString(ML, H - 92 * mm, "DOCUMENTAZIONE TECNICA")
    c.setFillColor(colors.white)
    c.setFont("Arial-Bold", 30)
    c.drawString(ML, H - 106 * mm, "Anonimizzazione di")
    c.drawString(ML, H - 118 * mm, "documenti clinici")
    c.setFont("Arial", 13)
    c.drawString(
        ML,
        H - 131 * mm,
        "Servizio anonymization-pipeline  -  Piattaforma MIA, evoluzione C1",
    )
    c.setStrokeColor(CYAN)
    c.setLineWidth(2)
    c.line(ML, H - 140 * mm, ML + 40 * mm, H - 140 * mm)
    c.setFont("Arial", 9.5)
    meta = [
        ("Cliente", "AGENAS  -  Piattaforma MIA (Medical Information Access)"),
        ("Versione", "Ottobre 2026  -  branch ref/backend"),
        ("Destinatari", "team di sviluppo, team piattaforma, referenti tecnici"),
        ("Classificazione", "Confidenziale"),
    ]
    y = H - 152 * mm
    for k, v in meta:
        c.setFont("Arial-Bold", 9.5)
        c.drawString(ML, y, k)
        c.setFont("Arial", 9.5)
        c.drawString(ML + 30 * mm, y, v)
        y -= 6 * mm
    c.restoreState()


def on_page(c, doc):
    c.saveState()
    c.setStrokeColor(DEEP)
    c.setLineWidth(1.2)
    c.line(ML, H - 14 * mm, W - MR, H - 14 * mm)
    c.setFont("Arial", 7)
    c.setFillColor(MUTED)
    c.drawString(ML, H - 12 * mm, "ANONYMIZATION-PIPELINE  |  DOCUMENTAZIONE TECNICA")
    c.drawRightString(W - MR, H - 12 * mm, "OTTOBRE 2026")
    c.drawImage(
        str(ASSETS / "bvtech-logo-teal.png"),
        ML,
        10 * mm,
        26 * mm,
        26 * mm * 218 / 1204,
        mask="auto",
    )
    c.setFont("Arial", 8)
    c.setFillColor(DEEP)
    c.drawRightString(W - MR, 10.4 * mm, f"{FOOTER}     {doc.page}")
    c.setFont("Arial", 6.3)
    c.setFillColor(MUTED)
    c.drawCentredString(W / 2, 16.5 * mm, CONFIDENTIAL)
    c.restoreState()


# --- content ----------------------------------------------------------------

CW = W - ML - MR
s: list = []

s += [NextPageTemplate("content"), PageBreak()]

# Sommario + 1
s.append(Paragraph("SOMMARIO", h1))
_toc = [
    ("1", "Scopo e contesto"),
    ("2", "Architettura: due pod"),
    ("3", "Flusso di elaborazione"),
    ("4", "Server OCR"),
    ("5", "Rilevamento dei dati personali"),
    ("6", "Redazione e verifica fail-closed"),
    ("7", "API REST"),
    ("8", "Privacy, logging e KPI"),
    ("9", "Deploy, configurazione e avvio"),
    ("10", "Risultati e limiti noti"),
]
_t = Table(
    [
        [Paragraph(f'<font color="#33B0B8"><b>{n}</b></font>', toc), Paragraph(t, toc)]
        for n, t in _toc
    ],
    colWidths=[9 * mm, CW - 9 * mm],
)
_t.setStyle(
    TableStyle(
        [
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]
    )
)
s.append(_t)
s.append(Spacer(1, 12))

s.append(H1("1", "Scopo e contesto"))
s.append(
    P(
        "<b>anonymization-pipeline</b> è il servizio della piattaforma MIA (AGENAS) che implementa il capitolo "
        "<b>C1 - Anonimizzazione</b> delle evoluzioni AI. Un medico carica un referto, una lettera di dimissione o una "
        "prescrizione (PDF nativo, PDF scansionato o foto); il servizio trova i dati personali e li rimuove dal file. "
        "Le pagine che contengono dati personali sono sostituite da un'immagine di se stesse con i riquadri neri "
        "dipinti sopra: impaginazione, colori e tabelle restano identici e un layer di testo invisibile le mantiene "
        "ricercabili. Le pagine senza dati personali restano come sono."
    )
)
s.append(H2("Principi di progetto"))
s += bullets(
    [
        (
            "<b>Fail-closed.</b> Ogni output è verificato prima di poter essere scaricato. Se la verifica fallisce, "
            "il documento è bloccato. Se il servizio NER non risponde, il job fallisce senza rilasciare nulla."
        ),
        (
            "<b>On-premise e open source.</b> Tutti i modelli girano nel cluster, su componenti con licenze permissive "
            "(pypdfium2, pypdf, LiteParse, RapidOCR con modelli PP-OCRv5)."
        ),
        (
            "<b>Stateless.</b> Nessun database: i job vivono in memoria e i documenti in una cartella privata fino "
            "alla scadenza (TTL, 30 minuti di default)."
        ),
        (
            "<b>Privacy by design.</b> Nessun testo, valore o nome di file nei log; URL con soli identificativi casuali; "
            "messaggi d'errore generici in italiano."
        ),
    ]
)
s.append(H2("Due modalità"))
s.append(
    table(
        [
            ["Modalità", "Cosa fa", "Output"],
            [
                "accurate (default)",
                (
                    "Pipeline completa: rilevamento su testo e pixel, redazione del PDF, verifica, "
                    "revisione affiancata con modifiche manuali."
                ),
                "PDF anonimizzato + copia Word",
            ],
            [
                "fast",
                (
                    "Stessi layer di testo e rilevamento testuale (regole, etichette, NER, propagazione), senza "
                    "rilevatori visivi, senza redazione del PDF e senza controlli sui pixel."
                ),
                "Markdown con delimitatori di pagina, tabelle e segnaposto [IMMAGINE]",
            ],
        ],
        [32 * mm, CW - 32 * mm - 52 * mm, 52 * mm],
        bold_first=True,
    )
)

# 2 Architettura
s.append(PageBreak())
s.append(H1("2", "Architettura: due pod"))
s.append(
    P(
        "Il sistema è composto da due deployable. Il <b>backend</b> espone l'API e contiene il motore di "
        "anonimizzazione; non carica modelli e non usa GPU. Il <b>server OCR</b> legge le pagine scansionate ed è "
        "l'unico componente che può usare una GPU. Il backend lo chiama via HTTP sul Service <font name='Mono'>paddle-ocr</font> "
        "dello stesso namespace. Il riconoscimento delle entità (NER) è delegato al servizio NER AILAB già in uso "
        "nella piattaforma."
    )
)
s.append(Spacer(1, 4))
s.append(architecture())
s.append(Spacer(1, 8))
s.append(
    table(
        [
            ["Componente", "Immagine / porta", "Ruolo", "Risorse"],
            [
                "Backend (API + motore)",
                "anonymization-pipeline<br/>:8000",
                (
                    "Upload, coda dei job, progresso SSE, "
                    "anonimizzazione, verifica, download, eventi KPI"
                ),
                "nessuna GPU; 1-4 CPU, 4-8 Gi (da rimisurare)",
            ],
            [
                "Server OCR",
                "paddle-ocr-server<br/>:8829",
                (
                    "PP-OCRv5 (detector server, recognizer latino) dietro "
                    "l'API OCR di LiteParse"
                ),
                "GPU opzionale (picco circa 4 GB per pagina); 1-2 CPU, 3-6 Gi",
            ],
            [
                "NER AILAB",
                "servizio esistente",
                "Entità su testo: persone, luoghi, indirizzi, ragioni sociali",
                "gestito da AILAB",
            ],
        ],
        [32 * mm, 34 * mm, CW - 32 * mm - 34 * mm - 42 * mm, 42 * mm],
        bold_first=True,
    )
)
s.append(H2("Struttura del repository"))
s.append(
    P(
        "Una cartella per pod, ciascuna con la struttura di ai-platform-v2: un progetto uv con il proprio lock, "
        "codice in un pacchetto <font name='Mono'>src/</font> servito come <font name='Mono'>src.main:app</font>, "
        "test che rispecchiano <font name='Mono'>src/</font>, <font name='Mono'>Dockerfile.api</font>, justfile e "
        "AGENTS.md. Il motore è un sotto-pacchetto autonomo del backend: non importa mai il servizio né un "
        "framework web (vincolo imposto da ruff), così può essere estratto da solo."
    )
)
s.append(
    Preformatted(
        "backend/                 pod 1, immagine anonymization-pipeline (contesto: backend/)\n"
        "  src/  main, lifespan, config, errors\n"
        "        router/          HTTP: health, anonymization (sessioni, stream SSE), errors\n"
        "        models/          modelli wire dell'API\n"
        "        services/        anonymization.py (sessioni, coda, KPI), anonymizer/ (motore)\n"
        "        llms/            client NER (ner.py) e server OCR (ocr.py)\n"
        "        utils/           logging con filtro PII, privacy, request id\n"
        "  tests/  evals/         test (come src/), CLI di valutazione\n"
        "ocr-server/              pod 2, immagine paddle-ocr-server, stessa struttura:\n"
        "  src/  router/ (ocr, health), services/ (lettura pagina), llms/ (RapidOCR)\n"
        "infra/                   docker-compose.yml dei due pod\n"
        "docs/  scripts/          documentazione, avvio locale\n"
        "justfile  .gitlab-ci.yml  sonar-project.properties",
        code,
    )
)

# 3 Flusso
s.append(PageBreak())
s.append(H1("3", "Flusso di elaborazione"))
s.append(
    P(
        "Il rilevamento lavora su testo semplice, la redazione lavora sul PDF. Il collegamento è il "
        "<b>layer di testo</b>: il testo della pagina in cui ogni carattere conserva il proprio riquadro. "
        "Uno span rilevato <font name='Mono'>(inizio, fine)</font> diventa un rettangolo per riga."
    )
)
s.append(pipeline())
s.append(Spacer(1, 6))
s.append(H2("Layer di testo"))
s += bullets(
    [
        (
            "<b>Pagine native</b>: pypdfium2 legge il layer di testo del PDF con un riquadro esatto per carattere. Sono "
            "inclusi il testo invisibile (bianco, render mode 3), i valori dei campi modulo e le annotazioni visibili."
        ),
        (
            "<b>Pagine scansionate</b> (immagine a pagina intera o immagini oltre metà pagina, secondo LiteParse): "
            "pagina renderizzata a 300 DPI e letta dal server OCR, una box per parola. I margini laterali sono riletti "
            "ruotati di 90 gradi; pagine e righe capovolte sono rilette girate di 180 gradi."
        ),
        "Una scansione con un layer OCR nascosto riceve entrambi i layer: anche il testo nascosto viene redatto.",
        (
            "Le righe sono ricostruite dalla geometria, in ordine di lettura: le celle di tabella restano separate e "
            "un'etichetta precede il suo valore."
        ),
        (
            '<b>Caratteri in caselle</b> (codice fiscale o tessera sanitaria su moduli, "R S S M R A ...") sono uniti in '
            "una parola. Sulle scansioni le caselle sono trovate dalla rigatura del modulo (OpenCV), ripulite dalle "
            "macchie e rilette come parola unica dal server OCR (<font name='Mono'>POST /recognize</font>)."
        ),
        (
            "Le <b>immagini cliniche in scala di grigi</b> dentro pagine native (RX, TC, ecografie) restano visibili; il "
            "testo impresso nei pixel viene letto dal server OCR e redatto. Le foto a colori sono redatte per intero (FACE)."
        ),
    ]
)
# 4 OCR
s.append(Spacer(1, 6))
s.append(H1("4", "Server OCR"))
s.append(
    P(
        "Il server (<font name='Mono'>ocr-server/</font>) espone PP-OCRv5 su RapidOCR (ONNX Runtime) dietro "
        "l'API OCR di LiteParse. Il contratto è fissato da test su entrambi i lati "
        "(<font name='Mono'>backend/src/services/anonymizer/OCR_CONTRACT.md</font>). Il testo letto non viene mai registrato."
    )
)
s.append(
    table(
        [
            ["Endpoint", "Input", "Risposta"],
            [
                "POST /ocr",
                "PNG della pagina, header X-Render-DPI",
                (
                    '{"results": [{text, bbox, confidence, polygon}]}, una voce per parola, coordinate in pixel; '
                    "l'ordine dei punti del polygon indica la direzione di lettura"
                ),
            ],
            [
                "POST /recognize",
                "PNG di una sola riga o parola",
                (
                    '{"text", "confidence"}: solo riconoscimento, '
                    "usato per le caselle dei codici"
                ),
            ],
            ["GET /health", "-", "200 quando i modelli sono caricati, altrimenti 503"],
        ],
        [27 * mm, 45 * mm, CW - 72 * mm],
        bold_first=True,
    )
)
s.append(Spacer(1, 4))
s += bullets(
    [
        (
            "Il classificatore di orientamento delle righe di RapidOCR è disattivato: girava righe dritte di 180 gradi, "
            "che venivano poi lette come rumore e scartate insieme ai dati personali."
        ),
        (
            "Una pagina con confidenza media sotto 0,8 viene riletta girata di 180 gradi; la lettura girata è tenuta solo "
            "se è sicura e più ricca. Su una pagina dritta, una riga sotto 0,5 viene riletta girata (etichetta incollata "
            "al contrario); se la rilettura non è sicura la riga è scartata come rumore."
        ),
        (
            "I modelli (circa 90 MB) sono letti da <font name='Mono'>/models/rapidocr</font>: il cluster non ha "
            "accesso a internet, quindi il volume va popolato prima del primo avvio."
        ),
    ]
)

# 4 Rilevamento
s.append(PageBreak())
s.append(H1("5", "Rilevamento dei dati personali"))
s.append(
    P(
        "Prima la recall: basta un rilevatore per redigere. I rilevatori testuali e visivi lavorano in "
        "parallelo e i loro risultati sono uniti; gli span sovrapposti confluiscono nell'etichetta più specifica."
    )
)
s.append(
    table(
        [
            ["Rilevatore", "Cosa trova"],
            [
                "Regole di formato",
                (
                    "Codice fiscale con checksum (più un pattern tollerante all'OCR), tessera sanitaria, "
                    "telefoni italiani ed esteri, email, IBAN, URL con token personali, date di nascita, età, indirizzi, "
                    "CAP + comune, iscrizioni professionali (OMCeO), NRE, campi DICOM"
                ),
            ],
            [
                "Etichetta - valore",
                (
                    'Il valore dopo circa 60 gruppi di etichette italiane e inglesi ("Cognome:", '
                    '"N. accettazione", "Prenotazione CUP", "Medico curante"), con la forma attesa del valore '
                    "(nome, luogo, indirizzo, identificativo, data, telefono)"
                ),
            ],
            [
                "NER AILAB",
                (
                    "Persone, luoghi, indirizzi, ragioni sociali via POST /batch_ner, una richiesta per pagina. "
                    "Gli offset sono riallineati sul testo originale. Retry su timeout e 502/503/504, poi fail-closed"
                ),
            ],
            [
                "Filtro eponimi",
                (
                    'Per occorrenza: "segno di Giordano" resta, "Sig. Giordano" va redatto. '
                    "Circa 400 eponimi, 50 scale cliniche, 200 nomi commerciali di farmaci, santi nei nomi di strutture"
                ),
            ],
            [
                "Propagazione",
                (
                    "Ogni nome e identificativo trovato è cercato in tutto il documento: spaziato, incollato, "
                    "a capo, con iniziali, con confusioni OCR (0/o, 1/l, 5/s), stesso schema di numerazione"
                ),
            ],
            [
                "Rilevatori visivi",
                (
                    "Codici a barre e QR (zxing), firme e timbri (immagini incorporate, disegni vettoriali, "
                    "inchiostro non spiegato dall'OCR, timbri con cornice o inchiostro colorato), foto a colori"
                ),
            ],
        ],
        [34 * mm, CW - 34 * mm],
        bold_first=True,
    )
)
s.append(H2("Tassonomia"))
s.append(
    table(
        [
            ["Etichette", "Tipo", "Redatte di default"],
            [
                "PERSON, FISCAL_CODE, HEALTH_CARD, ID_DOCUMENT, DATE_OF_BIRTH",
                "testo",
                "sì",
            ],
            ["ADDRESS, LOCATION, PHONE, EMAIL", "testo", "sì"],
            [
                "MEDICAL_RECORD, PRESCRIPTION_ID, PROFESSIONAL_ID, IBAN, URL",
                "testo",
                "sì",
            ],
            [
                "AGE, DATE (date cliniche), ORGANIZATION",
                "testo",
                "no: quasi-identificatori, configurabili",
            ],
            ["SIGNATURE, STAMP, BARCODE, QRCODE, FACE, HANDWRITING", "visivo", "sì"],
        ],
        [CW - 22 * mm - 52 * mm, 22 * mm, 52 * mm],
    )
)
s.append(Spacer(1, 4))
s.append(
    P(
        "Le date cliniche, l'età e le strutture sanitarie vengono rilevate ma lasciate visibili di default; "
        "si redigono impostando <font name='Mono'>ANONPIP_REDACT_CLINICAL_DATES</font>, "
        "<font name='Mono'>ANONPIP_REDACT_AGE</font> e <font name='Mono'>ANONPIP_REDACT_ORGANIZATIONS</font>.",
        note,
    )
)

# 5 Redazione e verifica
s.append(PageBreak())
s.append(H1("6", "Redazione e verifica fail-closed"))
s.append(H2("Redazione"))
s += bullets(
    [
        (
            "<b>Propagazione</b>: ogni occorrenza di un valore redatto riceve un riquadro, anche dove il rilevamento non "
            "l'aveva trovata (incollata a un'etichetta, spezzata dall'OCR, in un'intestazione fax)."
        ),
        (
            "<b>Pagine con riquadri</b>: renderizzate a 200 DPI senza perdita, riquadri dipinti sopra, sostituite "
            "dall'immagine. Della pagina originale sopravvivono solo i pixel; un layer di testo invisibile con le "
            "parole non coperte la mantiene ricercabile."
        ),
        "<b>Pagine senza riquadri</b>: copiate come sono.",
        (
            "<b>Ricostruzione del file</b> con pypdf a partire dalle sole pagine: nessun metadato, XMP, segnalibro, "
            "struttura, modulo, allegato, JavaScript, annotazione o versione incrementale precedente."
        ),
        "Le aree disegnate a mano sono estese al riquadro completo di ogni carattere che toccano.",
    ]
)
s.append(H2("Controlli prima del rilascio"))
s.append(
    table(
        [
            ["#", "Controllo"],
            [
                "1",
                "Nessun testo originale leggibile sotto un riquadro (testo orizzontale e verticale)",
            ],
            ["2", "Nessun testo fuori dall'area visibile della pagina"],
            [
                "3",
                "Nessun valore redatto nel testo di output, nei byte decompressi del file o nei metadati",
            ],
            ["4", "Nessuna annotazione rimasta"],
            [
                "5",
                "Pagine renderizzate identiche fuori dai riquadri (tolleranza ANONPIP_VERIFY_PIXEL_TOLERANCE)",
            ],
            [
                "6",
                "Nessun valore redatto nell'export Word (o nel Markdown in modalità fast)",
            ],
        ],
        [10 * mm, CW - 10 * mm],
    )
)
s.append(Spacer(1, 6))
s.append(
    P(
        "<b>Seconda passata.</b> Se un controllo fallisce, il valore ancora presente viene coperto dove il "
        "controllo lo ha trovato, le pagine senza riquadri che hanno fallito vengono anch'esse appiattite e il "
        "file è ricostruito e verificato di nuovo. Il risultato riporta le pagine appiattite "
        "(<font name='Mono'>flattened_pages</font>)."
    )
)
s.append(
    callout(
        "<b>Blocco.</b> Un documento che fallisce anche la seconda verifica non è scaricabile "
        "(409 <font name='Mono'>VERIFICATION_FAILED</font>) e le ragioni sono mostrate a chi lo ha "
        "caricato. Una modifica manuale rende subito la sessione non verificata: nulla è scaricabile finché "
        "la nuova versione non supera tutti i controlli."
    )
)

# 7 API
s.append(PageBreak())
s.append(H1("7", "API REST"))
s.append(
    P(
        "Gli endpoint seguono il document agent di ai-platform-v2: l'upload crea una <b>sessione</b>, il lavoro "
        "avviene dentro una POST che restituisce uno <b>stream</b> Server-Sent Events, il risultato si legge con "
        "<font name='Mono'>preview</font> e <font name='Mono'>download</font>. Prefisso "
        "<font name='Mono'>/anonymizations</font>; la modalità si sceglie all'upload (campo "
        "<font name='Mono'>mode</font>, <font name='Mono'>accurate|fast</font>); un'azione dell'altra modalità "
        "risponde 409 <font name='Mono'>MODE_NOT_SUPPORTED</font>. Endpoint di piattaforma: "
        "<font name='Mono'>/healthz</font>, <font name='Mono'>/readyz</font>, <font name='Mono'>/version</font>."
    )
)
s.append(
    table(
        [
            ["Metodo e path", "Cosa fa", "Modalità"],
            [
                "POST /sessions",
                "Upload multipart (file, mode): crea la sessione; 201, status created",
                "entrambe",
            ],
            [
                "GET /sessions/{id}",
                "Stato della sessione; include il risultato a fine elaborazione",
                "entrambe",
            ],
            [
                "POST /sessions/{id}/anonymize/stream",
                "Avvia l'anonimizzazione; stream SSE status, poi done o error",
                "entrambe",
            ],
            [
                "POST /sessions/{id}/edits/stream",
                "Correzioni {add, remove, restore, search}; stream SSE come sopra",
                "accurate",
            ],
            [
                "POST /sessions/{id}/cancel",
                "Interrompe al passo successivo; sessione e file cancellati",
                "entrambe",
            ],
            [
                "GET /sessions/{id}/preview",
                "Documento rilasciato inline: PDF (accurate) o Markdown (fast)",
                "entrambe",
            ],
            [
                "GET /sessions/{id}/download?format=",
                "Allegato pdf | docx (accurate), md (fast); solo se verificato",
                "entrambe",
            ],
            [
                "GET /sessions/{id}/review",
                "Manifest della revisione affiancata: pagine, riquadri, etichette, versione",
                "accurate",
            ],
            [
                "GET /sessions/{id}/pages/{which}/{n}",
                "PNG della pagina original o anonymized (solo accurate)",
                "entrambe",
            ],
            ["DELETE /sessions/{id}", "Cancellazione immediata", "entrambe"],
            ["GET /labels", "Tassonomia: [{code, title, kind, redact}]", "-"],
        ],
        [62 * mm, CW - 62 * mm - 19 * mm, 19 * mm],
    )
)
s.append(H2("Stream SSE"))
s += bullets(
    [
        (
            "Ogni frame è <font name='Mono'>event: &lt;nome&gt;</font> più una riga <font name='Mono'>data</font> "
            "JSON. <font name='Mono'>status</font>: <font name='Mono'>{step, percent, text}</font>, avanzamento con "
            "un testo in italiano. <font name='Mono'>done</font>: la sessione con il risultato e i tempi. "
            "<font name='Mono'>error</font>: <font name='Mono'>{error_title, error_message, error_code}</font>, "
            "titolo e messaggio generici, mai il testo del documento."
        ),
        (
            "Una sessione già conclusa risponde subito <font name='Mono'>done</font>; una sessione già in lavorazione "
            "risponde <font name='Mono'>error</font> <font name='Mono'>SESSION_BUSY</font>."
        ),
        (
            "Se il client si disconnette si chiude solo lo stream: il lavoro prosegue e "
            "<font name='Mono'>GET /sessions/{id}</font> ne riporta lo stato."
        ),
    ]
)
s.append(H2("Revisione affiancata (contratto con il frontend)"))
s += bullets(
    [
        (
            "Il client crea la sessione, segue lo stream <font name='Mono'>anonymize/stream</font> fino al frame "
            "<font name='Mono'>done</font>, poi legge <font name='Mono'>/review</font>: originale a sinistra, "
            "anonimizzato a destra."
        ),
        (
            "<font name='Mono'>rect</font> e pagine sono in punti PDF, origine in alto a sinistra (scala "
            "<font name='Mono'>larghezza_immagine_px / width</font>); i riquadri non contengono testo. Il revisore "
            "disegna un'area o clicca un riquadro (<font name='Mono'>edits/stream</font>); "
            "<font name='Mono'>version</font> e gli URL delle pagine cambiano a ogni modifica."
        ),
    ]
)
s.append(H2("Codici di errore"))
s.append(
    table(
        [
            ["Codice", "HTTP", "Quando"],
            [
                "UNSUPPORTED_FORMAT / FILE_TOO_LARGE / TOO_MANY_PAGES",
                "415 / 413 / 413",
                "upload non valido",
            ],
            [
                "UNREADABLE_DOCUMENT / PASSWORD_PROTECTED",
                "422",
                "file illeggibile o cifrato",
            ],
            ["TOO_MANY_JOBS", "429", "coda piena (ANONPIP_MAX_JOBS)"],
            [
                "JOB_NOT_FOUND",
                "404",
                "id sconosciuto, scaduto o cancellato nel frattempo",
            ],
            [
                "JOB_NOT_READY / MODE_NOT_SUPPORTED",
                "409",
                "sessione non conclusa o modifica in corso / azione dell'altra modalità",
            ],
            ["VERIFICATION_FAILED", "409", "verifica fail-closed non superata"],
            [
                "NER_UNAVAILABLE / SERVICE_UNAVAILABLE",
                "503 / frame error",
                "dipendenza giù: riprovare più tardi",
            ],
            [
                "PROCESSING_FAILED / SESSION_BUSY / CANCELLED",
                "frame error",
                "errore non classificato / sessione già in lavorazione / annullata",
            ],
        ],
        [70 * mm, 26 * mm, CW - 96 * mm],
    )
)

# 8 Privacy
s.append(PageBreak())
s.append(H1("8", "Privacy, logging e KPI"))
s.append(H2("Ciclo di vita dei documenti"))
s += bullets(
    [
        "Un job alla volta (un worker per pod); anche le modifiche passano dallo stesso worker.",
        (
            "I documenti vivono in una cartella privata (permessi 0700) solo finché il job esiste; sono cancellati alla "
            "scadenza (<font name='Mono'>ANONPIP_JOB_TTL_MINUTES</font>), su DELETE, su cancel e allo spegnimento."
        ),
        "I job sono in memoria: con più di una replica l'ingress richiede session affinity.",
        (
            "Gli URL contengono solo identificativi casuali di 32 caratteri esadecimali. I download hanno un nome "
            "generico (<font name='Mono'>documento_anonimizzato.pdf</font>), perché il nome originale può contenere "
            "il nome del paziente."
        ),
        (
            "Il testo di ogni pagina esce dal pod solo verso il servizio NER AILAB, all'interno del cluster; il pod OCR "
            "riceve immagini di pagina, non scrive su disco e non registra il testo letto."
        ),
    ]
)
s.append(H2("Regole di logging"))
s.append(
    table(
        [
            ["Livello / categoria", "Regola"],
            [
                "Mai, in nessun ambiente",
                (
                    "Codici fiscali in chiaro, nomi, email, JWT, chiavi, header grezzi, body di "
                    "richiesta o risposta, stringhe di connessione, input di un errore di validazione"
                ),
            ],
            [
                "Contenuto di debug",
                (
                    "Testo inserito dall'utente solo in locale e staging, a livello DEBUG, tramite "
                    "redact_user_text e con ANONPIP_EXTENDED_LOG_CONTEXT=true. In prod sempre &lt;redacted&gt;"
                ),
            ],
            [
                "Identificativi personali",
                "hash_identifier (SHA-256, stessa normalizzazione di authz) o mask_identifier (R***U)",
            ],
            [
                "Rete di sicurezza",
                (
                    "PiiRedactingFilter maschera i codici fiscali in tutto ciò che arriva su stdout, "
                    "traceback e uvicorn compresi"
                ),
            ],
            [
                "Errori di elaborazione",
                "Solo il tipo di eccezione: i messaggi possono contenere testo del documento",
            ],
        ],
        [40 * mm, CW - 40 * mm],
        bold_first=True,
    )
)
s.append(H2("Eventi KPI (contratto con KPI-dashboard)"))
s.append(
    P(
        "Formato <font name='Mono'>nome_evento {json}</font> su stdout, inviato a Loki da Grafana Alloy. "
        "Nomi e chiavi non si rinominano; ogni evento è coperto da un test che verifica anche l'assenza di PII."
    )
)
s.append(
    table(
        [
            ["Evento", "Chiavi"],
            [
                "anonymization_job_created",
                "job_id, pages, size_kb, source_format, mode",
            ],
            [
                "anonymization_completed",
                (
                    "job_id, pages, scanned_pages, boxes, values_by_label, verified, edits, "
                    "seconds_total, timings, mode"
                ),
            ],
            [
                "anonymization_edited",
                "job_id, added, removed, restored, search_hits, verified, seconds",
            ],
            ["anonymization_failed", "job_id, error (tipo di eccezione)"],
            [
                "anonymization_cancelled / anonymization_job_deleted",
                "job_id / job_id, reason (user, expired)",
            ],
        ],
        [62 * mm, CW - 62 * mm],
    )
)

# 9 Deploy
s.append(PageBreak())
s.append(H1("9", "Deploy, configurazione e avvio"))
s.append(
    P(
        "Gli sviluppatori non modificano i repository Flux (<font name='Mono'>oke-staging</font>, "
        "<font name='Mono'>oke-prod</font>): consegnano la scheda <font name='Mono'>docs/DEPLOY_HANDOVER.md</font> "
        "al team piattaforma, che crea namespace, manifest, image policy e Kustomization. Il servizio non ha "
        "segreti né database."
    )
)
s.append(
    table(
        [
            ["", "Pod backend", "Pod OCR"],
            [
                "Immagine",
                "harbor.agenas.oci/agenas/anonymization-pipeline",
                "harbor.agenas.oci/agenas/paddle-ocr-server",
            ],
            [
                "Build",
                "docker build -f backend/Dockerfile.api --target runtime backend",
                "docker build -f ocr-server/Dockerfile.api --build-arg OCR_EXTRA=gpu ocr-server",
            ],
            [
                "Probe",
                "/healthz (liveness), /readyz (503 finché OCR e NER non rispondono)",
                "/health (503 finché i modelli non sono caricati)",
            ],
            ["Repliche", "1, oppure 2+ con session affinity", "1"],
            [
                "Storage",
                "emptyDir per ANONPIP_WORK_DIR",
                "volume dei modelli su /models",
            ],
            [
                "Esposizione",
                "Ingress interno / WSO2",
                "solo Service interno paddle-ocr",
            ],
        ],
        [22 * mm, (CW - 22 * mm) / 2, (CW - 22 * mm) / 2],
        bold_first=True,
    )
)
s.append(H2("Variabili principali"))
s.append(
    table(
        [
            ["Variabile", "Default", "Note"],
            [
                "ANONPIP_ENVIRONMENT",
                "prod",
                "il più conservativo: redazione log attiva, DEBUG e OpenAPI spenti",
            ],
            [
                "ANONPIP_OCR_SERVER_URL",
                "vuoto",
                "http://paddle-ocr:8829 in cluster; vuoto = /readyz 503",
            ],
            [
                "ANONPIP_NER_URL",
                "vuoto",
                "URL interno del servizio NER AILAB; vuoto = /readyz 503",
            ],
            [
                "ANONPIP_NER_RETRIES / _TIMEOUT_SECONDS",
                "2 / 30",
                "retry su timeout e 502/503/504, poi il job fallisce",
            ],
            [
                "ANONPIP_MAX_UPLOAD_MB / MAX_PAGES / MAX_JOBS",
                "30 / 50 / 20",
                "limiti dell'upload e della coda",
            ],
            [
                "ANONPIP_JOB_TTL_MINUTES",
                "30",
                "dopo la scadenza i documenti sono cancellati",
            ],
            [
                "OCRSRV_DEVICE",
                "auto",
                "GPU se disponibile, altrimenti CPU; gpu = termina senza GPU",
            ],
            [
                "OCRSRV_GPU_MEM_LIMIT_MB",
                "0",
                "tetto opzionale della memoria GPU (almeno 4500 se usato)",
            ],
        ],
        [64 * mm, 24 * mm, CW - 88 * mm],
    )
)
s.append(H2("Rilascio"))
s.append(
    P(
        "Feature branch, poi MR verso <font name='Mono'>test</font>: la CI esegue lint, tipi, test e Sonar "
        "per i soli servizi toccati, costruisce <font name='Mono'>YYYY.MM.DD-test.&lt;epoch&gt;</font> e Flux "
        "rilascia in staging. Dopo la validazione, MR da <font name='Mono'>test</font> a "
        "<font name='Mono'>main</font>: tag <font name='Mono'>YYYY.MM.DD</font> e rilascio in produzione. "
        "L'immagine OCR si ricostruisce solo quando cambia <font name='Mono'>ocr-server/</font>."
    )
)
s.append(H2("Avvio locale"))
s.append(
    table(
        [
            ["Comando", "Cosa fa"],
            [
                "just start",
                "dipendenze, server OCR sull'host (GPU se presente), API sulla prima porta libera, pagina /docs",
            ],
            [
                "just ocr::serve  +  just backend::dev",
                "i due processi separati, come i due pod (API con reload)",
            ],
            [
                "just up / just down",
                "docker compose (infra/docker-compose.yml) con i container app e paddle-ocr",
            ],
            [
                "just backend::models",
                "verifica che server OCR e servizio NER rispondano",
            ],
            [
                "just backend::lint / backend::test-cov",
                "ruff + ty; test con copertura minima 80% (idem just ocr::...)",
            ],
            [
                "just backend::evaluate",
                "valutazione sul gold set copiato in dataset/ (fuori da git); report in reports/",
            ],
        ],
        [46 * mm, CW - 46 * mm],
    )
)

# 10 Risultati
s.append(PageBreak())
s.append(H1("10", "Risultati e limiti noti"))
s.append(
    P(
        "Misure con <font name='Mono'>just backend::evaluate</font> sull'insieme di valutazione: 200 documenti "
        "sintetici (326 pagine), ciascuno come PDF nativo, scansione e scansione con layer OCR nascosto, per "
        "600 PDF. 185 documenti in italiano e 15 in inglese, con trappole mirate: testo bianco, metadati, note "
        "cancellate, cognomi uguali a eponimi, codici in caselle, fax. Ground truth esatta, generata dallo "
        "stesso seed del documento. Valutazione del 22/10/2026, servizio NER AILAB, server OCR su GPU "
        "RTX 5070 laptop (8 GB)."
    )
)
s.append(
    table(
        [
            ["Metrica", "Nativo", "Scansione", "Scansione + layer OCR"],
            ["Recall (box, PII obbligatori)", "0,940", "0,918", "0,933"],
            ["Precision (box)", "0,954", "0,926", "0,872"],
            ["F1 (box)", "0,947", "0,922", "0,902"],
            ["Accuracy (parola)", "0,986", "0,960", "0,951"],
            ["AP@0.5 / mAP@0.5", "0,663 / 0,674", "0,595 / 0,582", "0,519 / 0,568"],
            ["PII nascosti trapelati", "1 / 324", "n/a", "0 / 324"],
            ["Eponimi redatti per errore", "65 / 415", "77 / 415", "108 / 415"],
            [
                "Documenti che superano la verifica",
                "200 / 200",
                "200 / 200",
                "200 / 200",
            ],
            ["Secondi per pagina", "0,75", "2,54", "2,67"],
        ],
        [CW - 3 * 33 * mm, 33 * mm, 33 * mm, 33 * mm],
        bold_first=True,
    )
)
s.append(Spacer(1, 4))
s.append(
    callout(
        "Per tipo: codice fiscale 99% / 94% / 96% (nativo / scansione / scansione + layer), tessera "
        "sanitaria 100%, persone 96-97%, codici a barre, QR, URL e IBAN 97-100%. I più deboli sono i numeri "
        "interni di pratica (MEDICAL_RECORD, 74-79%), i documenti d'identità sulle scansioni (70%) e la "
        "scrittura a mano (0 su 21, coperta dalle modifiche manuali). La recall è la metrica di sicurezza "
        "(un valore mancato è una fuga); la precision misura la sovra-redazione."
    )
)
s.append(H2("Limiti noti"))
s += bullets(
    [
        "Dati personali scritti a mano sulle scansioni non sono rilevati: li coprono le modifiche manuali.",
        (
            'Il servizio NER AILAB può mancare o redigere in parte nomi che sostituisce come eponimi ("Corti", '
            '"Vittoria"), nomi stranieri ed elenchi fitti di pazienti. Se il servizio è giù, i job falliscono.'
        ),
        (
            "Le caselle dei codici sono trovate solo con rigatura scura e continua: griglie grigio chiaro o rosse e "
            "caselle tratteggiate restano alla lettura OCR di pagina."
        ),
        (
            "Le pagine con riquadri diventano immagini: restano ricercabili tramite il layer invisibile, ma non sono "
            "più testo vettoriale e i documenti nativi crescono di circa il 45%."
        ),
        (
            "Testo ruotato di angoli diversi da 90 e 180 gradi non è gestito. Input DOCX/TXT non supportato; la modalità "
            "reversibile (mappatura cifrata) è fuori ambito."
        ),
    ]
)


doc = BaseDocTemplate(
    str(OUT),
    pagesize=A4,
    leftMargin=ML,
    rightMargin=MR,
    topMargin=MT,
    bottomMargin=MB,
    title="Anonimizzazione di documenti clinici - Documentazione tecnica",
    author="BV TECH S.p.A.",
    subject="Servizio anonymization-pipeline, piattaforma MIA (AGENAS)",
)
frame = Frame(
    ML,
    MB,
    W - ML - MR,
    H - MT - MB,
    id="f",
    leftPadding=0,
    rightPadding=0,
    topPadding=0,
    bottomPadding=0,
)
doc.addPageTemplates(
    [
        PageTemplate(id="cover", frames=[Frame(0, 0, W, H, id="c")], onPage=on_cover),
        PageTemplate(id="content", frames=[frame], onPage=on_page),
    ]
)
doc.build(s)
