# /// script
# requires-python = ">=3.12"
# dependencies = ["reportlab>=4.2", "pyyaml>=6"]
# ///
"""Technical documentation PDF of the service (flow, architecture, components), BV TECH style.

Usage: ``uv run scripts/docs/architettura.py`` (or ``just docs-pdf``). Writes
``docs/architettura-anonymization-pipeline.pdf``, at most 5 A4 pages. Version, categories and
per-phase models are read from the repository, so the document follows the code when it is
regenerated.
"""

import datetime
import math
import tomllib
from pathlib import Path
from xml.sax.saxutils import escape

import yaml
from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[2]
RESOURCES = ROOT / "src" / "anonymization_pipeline" / "resources"
ASSETS = ROOT / "docs" / "assets" / "bvtech"
OUTPUT = ROOT / "docs" / "architettura-anonymization-pipeline.pdf"
MAX_PAGES = 5

# BV TECH palette
DEEP_TEAL = colors.HexColor("#005E73")
DARK_TEAL = colors.HexColor("#003E51")
MEDIUM_TEAL = colors.HexColor("#00808E")
LIGHT_TEAL = colors.HexColor("#009CA6")
CYAN = colors.HexColor("#33B0B8")
GREEN_TEAL = colors.HexColor("#00A17B")
MINT = colors.HexColor("#70C0A6")
MUTED_TEAL = colors.HexColor("#3A6973")
TINT = colors.HexColor("#E8F3F5")
GRID = colors.HexColor("#B9DADF")
WHITE = colors.white
BLACK = colors.black

PAGE_W, PAGE_H = A4
MARGIN = 46
FRAME_W = PAGE_W - 2 * MARGIN
BAND_H = 250
FOOTER_TEXT = "ANONYMIZATION PIPELINE · DOCUMENTAZIONE TECNICA"
CONFIDENTIAL = (
    "Confidenziale. Questo documento contiene informazioni riservate e non può essere "
    "riprodotto o diffuso senza autorizzazione."
)
MONTHS = (
    "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre "
    "dicembre"
).split()

_FONT_CANDIDATES = {
    "Arial": [
        "/mnt/c/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial.ttf",
    ],
    "Arial-Bold": [
        "/mnt/c/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ],
    "Mono": [
        "/mnt/c/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ],
}
_FALLBACK = {"Arial": "Helvetica", "Arial-Bold": "Helvetica-Bold", "Mono": "Courier"}


def _register_fonts() -> dict[str, str]:
    """Arial (brand font) and a monospace font when installed, the PDF base fonts otherwise."""
    names = {}
    for name, paths in _FONT_CANDIDATES.items():
        found = next((p for p in paths if Path(p).is_file()), None)
        if found:
            pdfmetrics.registerFont(TTFont(name, found))
            names[name] = name
        else:
            names[name] = _FALLBACK[name]
    return names


FONTS = _register_fonts()
FONT, FONT_B, MONO = FONTS["Arial"], FONTS["Arial-Bold"], FONTS["Mono"]
if FONT == "Arial":
    pdfmetrics.registerFontFamily("Arial", normal="Arial", bold="Arial-Bold")
ARROW = "→" if FONT == "Arial" else "-&gt;"


# ----------------------------------------------------------------------------- facts


def _facts() -> dict[str, object]:
    """Numbers and names read from the repository."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    categories = yaml.safe_load(
        (RESOURCES / "categories.yaml").read_text(encoding="utf-8")
    )
    models = yaml.safe_load((RESOURCES / "models.yaml").read_text(encoding="utf-8"))
    groups = categories["groups"]
    today = datetime.date.today()
    by_model: dict[str, list[str]] = {}
    labels = {
        "extraction": "estrazione",
        "detection": "rilevamento",
        "redaction": "censura",
        "verification": "verifica",
    }
    for phase, model in models["phases"].items():
        by_model.setdefault(model, []).append(labels[phase])
    return {
        "version": project["project"]["version"],
        "date": f"{MONTHS[today.month - 1].capitalize()} {today.year}",
        "categories": sum(len(c) for c in groups.values()),
        "groups": len(groups),
        "models": "; ".join(f"{', '.join(p)}: {m}" for m, p in by_model.items()),
    }


FACTS = _facts()


# ----------------------------------------------------------------------------- text styles

BODY = ParagraphStyle(
    "body", fontName=FONT, fontSize=9, leading=12.6, textColor=BLACK, alignment=TA_LEFT
)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10.6)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=7.8, leading=10)
CELL_HEAD = ParagraphStyle("cell_head", parent=CELL, fontName=FONT_B, textColor=WHITE)
H1 = ParagraphStyle(
    "h1",
    fontName=FONT_B,
    fontSize=15,
    leading=17,
    textColor=DEEP_TEAL,
    spaceAfter=7,
)
H2 = ParagraphStyle(
    "h2",
    fontName=FONT_B,
    fontSize=10,
    leading=13,
    textColor=DEEP_TEAL,
    spaceBefore=9,
    spaceAfter=4,
)


def c(text: str) -> str:
    """Inline code (module, endpoint, variable)."""
    return f'<font face="{MONO}" color="#3A6973">{escape(text)}</font>'


def h1(number: int, title: str) -> Paragraph:
    """Section title, ALL CAPS as the brand titles."""
    return Paragraph(
        f'<font color="#33B0B8">{number}</font>&nbsp;&nbsp;{escape(title.upper())}', H1
    )


def p(text: str, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(text, style)


def table(rows: list[list[str]], widths: list[float], header: bool = True) -> Table:
    """Brand table: Deep Teal header, alternating tinted rows, thin teal grid."""
    data = [
        [Paragraph(cell, CELL_HEAD if header and i == 0 else CELL) for cell in row]
        for i, row in enumerate(rows)
    ]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.6),
    ]
    first = 1 if header else 0
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), DEEP_TEAL))
    for i in range(first, len(rows)):
        if (i - first) % 2 == 1:
            style.append(("BACKGROUND", (0, i), (-1, i), TINT))
    t.setStyle(TableStyle(style))
    return t


class Kpis(Flowable):
    """KPI strip: Bright Cyan numbers and white labels on Dark Teal."""

    def __init__(self, items: list[tuple[str, str]], height: float = 56) -> None:
        super().__init__()
        self.items, self.height, self.width = items, height, FRAME_W

    def draw(self) -> None:
        canvas = self.canv
        canvas.setFillColor(DARK_TEAL)
        canvas.roundRect(0, 0, self.width, self.height, 5, stroke=0, fill=1)
        cell = self.width / len(self.items)
        for i, (number, label) in enumerate(self.items):
            x = i * cell + 14
            if i:
                canvas.setStrokeColor(MEDIUM_TEAL)
                canvas.setLineWidth(0.6)
                canvas.line(i * cell, 10, i * cell, self.height - 10)
            canvas.setFillColor(CYAN)
            canvas.setFont(FONT_B, 22)
            canvas.drawString(x, self.height - 29, number)
            canvas.setFillColor(WHITE)
            canvas.setFont(FONT_B, 7.6)
            for j, line in enumerate(simpleSplit(label, FONT_B, 7.6, cell - 24)):
                canvas.drawString(x, self.height - 41 - j * 9, line)


# ----------------------------------------------------------------------------- drawing helpers


def _box(
    d: Drawing,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    body: str = "",
    fill: colors.Color = DEEP_TEAL,
    color: colors.Color = WHITE,
    size: float = 8,
    stroke: colors.Color | None = None,
    dash: list[float] | None = None,
) -> None:
    """Rounded box with a centred bold title and an optional wrapped body."""
    d.add(
        Rect(
            x,
            y,
            w,
            h,
            rx=4,
            ry=4,
            fillColor=fill,
            strokeColor=stroke,
            strokeWidth=0.9 if stroke else 0,
            strokeDashArray=dash,
        )
    )
    small = size - 1.3
    titles = simpleSplit(title, FONT_B, size, w - 8)
    lines = simpleSplit(body, FONT, small, w - 8) if body else []
    total = len(titles) * (size + 1.6) + (
        len(lines) * (small + 1.6) + 2 if lines else 0
    )
    baseline = y + h / 2 + total / 2 - size * 0.82
    for line in titles:
        d.add(
            String(
                x + w / 2,
                baseline,
                line,
                fontName=FONT_B,
                fontSize=size,
                fillColor=color,
                textAnchor="middle",
            )
        )
        baseline -= size + 1.6
    baseline -= 2
    for line in lines:
        d.add(
            String(
                x + w / 2,
                baseline,
                line,
                fontName=FONT,
                fontSize=small,
                fillColor=color,
                textAnchor="middle",
            )
        )
        baseline -= small + 1.6


def _arrow(
    d: Drawing,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    color: colors.Color = MUTED_TEAL,
    label: str = "",
    dash: list[float] | None = None,
    label_dy: float = 3,
) -> None:
    """Line with an arrow head at (x2, y2) and an optional label above its middle."""
    d.add(
        Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=1.1, strokeDashArray=dash)
    )
    angle = math.atan2(y2 - y1, x2 - x1)
    head = 5.5
    points = [x2, y2]
    for side in (-0.42, 0.42):
        points += [
            x2 - head * math.cos(angle + side),
            y2 - head * math.sin(angle + side),
        ]
    d.add(Polygon(points, fillColor=color, strokeColor=color, strokeWidth=0.4))
    if label:
        d.add(
            String(
                (x1 + x2) / 2,
                (y1 + y2) / 2 + label_dy,
                label,
                fontName=FONT,
                fontSize=6.6,
                fillColor=MUTED_TEAL,
                textAnchor="middle",
            )
        )


def _zone(
    d: Drawing,
    x: float,
    y: float,
    w: float,
    h: float,
    label: str,
    fill: colors.Color,
    dash: list[float] | None = None,
    right: bool = False,
) -> None:
    """Translucent area (network, profile) with its label at the top."""
    tint = colors.Color(fill.red, fill.green, fill.blue, alpha=0.13)
    d.add(
        Rect(
            x,
            y,
            w,
            h,
            rx=6,
            ry=6,
            fillColor=tint,
            strokeColor=fill,
            strokeWidth=0.8,
            strokeDashArray=dash,
        )
    )
    d.add(
        String(
            x + w - 6 if right else x + 6,
            y + h - 10,
            label,
            fontName=FONT_B,
            fontSize=6.8,
            fillColor=fill,
            textAnchor="end" if right else "start",
        )
    )


def flow_diagram() -> Drawing:
    """The five phases, their engines and the two outcomes."""
    d = Drawing(FRAME_W, 236)
    phases = [
        ("1  Ingest", "PDF a 200 dpi, immagini, testo; pagina nativa o no"),
        ("2  Estrazione", "pagine native: docling; scansioni e immagini: VLM"),
        ("3  Rilevamento", "VLM, regole, etichette, NER in unione; ancoraggio"),
        ("4  Censura", "coreferenza VLM, tag coerenti, sostituzione meccanica"),
        ("5  Verifica", "il VLM confronta censurato e originale"),
    ]
    gap, w, h, y = 13, (FRAME_W - 4 * 13) / 5, 64, 132
    d.add(
        String(
            0,
            222,
            "Documento: PDF nativo o scansionato, immagine (png, jpg, tif, webp), testo",
            fontName=FONT_B,
            fontSize=7.4,
            fillColor=MUTED_TEAL,
        )
    )
    _arrow(d, w / 2, 216, w / 2, y + h + 2)
    shades = [DARK_TEAL, DEEP_TEAL, MEDIUM_TEAL, LIGHT_TEAL, CYAN]
    for i, (title, body) in enumerate(phases):
        x = i * (w + gap)
        _box(d, x, y, w, h, title, body, fill=shades[i], size=8.4)
        if i < 4:
            _arrow(d, x + w + 1, y + h / 2, x + w + gap - 1, y + h / 2)
    engines = [
        ("pypdfium2, Pillow", "thread CPU"),
        ("docling (CPU) || VLM (GPU)", "una chiamata VLM per pagina"),
        ("3 passate VLM per pagina", "filtro eponimi dal contesto"),
        ("mappa AES-256-GCM", "pseudonym | tag | redaction"),
        ("off | report | block", "block per default"),
    ]
    for i, (title, body) in enumerate(engines):
        x = i * (w + gap)
        _box(
            d,
            x,
            82,
            w,
            38,
            title,
            body,
            fill=TINT,
            color=DEEP_TEAL,
            size=7.2,
            stroke=GRID,
        )
        d.add(Line(x + w / 2, 120, x + w / 2, y, strokeColor=GRID, strokeWidth=0.8))
    ok_w = FRAME_W * 0.56
    _box(
        d,
        0,
        0,
        ok_w,
        56,
        "Esito: documento anonimizzato (ZIP o JSON)",
        "Markdown censurato, report e metriche senza valori, mappa cifrata in modalità "
        "pseudonimo; invariante verificata: nessun valore sostituito resta in chiaro",
        fill=GREEN_TEAL,
        size=8.2,
    )
    _box(
        d,
        ok_w + 14,
        0,
        FRAME_W - ok_w - 14,
        56,
        "Rifiuto fail-closed (HTTP 422)",
        "residui della verifica, invariante violata, verifica non disponibile: "
        "nessun valore nella risposta",
        fill=DARK_TEAL,
        size=8.2,
    )
    last = 4 * (w + gap) + w / 2
    _arrow(
        d,
        last - 22,
        82,
        ok_w - 30,
        58,
        color=GREEN_TEAL,
        label="ok o report",
        label_dy=-12,
    )
    _arrow(d, last + 14, 82, last + 14, 58, color=DARK_TEAL)
    return d


def _bus(
    d: Drawing,
    x1: float,
    x2: float,
    y: float,
    label: str,
    color: colors.Color,
    taps: list[tuple[float, float]],
    right: bool = False,
) -> None:
    """Network drawn as a bus line, with a connector down to each container it reaches."""
    d.add(Line(x1, y, x2, y, strokeColor=color, strokeWidth=3, strokeLineCap=1))
    d.add(
        String(
            x2 if right else x1,
            y + 6,
            label,
            fontName=FONT_B,
            fontSize=6.8,
            fillColor=color,
            textAnchor="end" if right else "start",
        )
    )
    for x, top in taps:
        d.add(Line(x, y, x, top, strokeColor=color, strokeWidth=1.2))
        d.add(Circle(x, y, 2.8, fillColor=color, strokeColor=WHITE, strokeWidth=0.8))


def deployment_diagram() -> Drawing:
    """Containers on their networks, volumes and the download profile."""
    d = Drawing(FRAME_W, 296)
    right = FRAME_W
    box_y, box_h, box_w = 196, 58, 112
    web_x, app_x, ollama_x = 86, 236, right - box_w
    mid = box_y + box_h / 2
    top = box_y + box_h
    # networks: separate buses, the app is the only container on both
    _bus(
        d,
        120,
        282,
        280,
        "rete edge: porte pubblicate solo su 127.0.0.1",
        LIGHT_TEAL,
        [(web_x + box_w / 2, top), (app_x + 26, top)],
    )
    _bus(
        d,
        302,
        right - 12,
        280,
        "rete internal: nessuna rotta verso internet",
        DEEP_TEAL,
        [(app_x + 86, top), (ollama_x + box_w / 2, top)],
        right=True,
    )
    # containers and requests
    _box(
        d, 0, box_y + 6, 54, box_h - 12, "Browser", "operatore", fill=MUTED_TEAL, size=8
    )
    _box(
        d,
        web_x,
        box_y,
        box_w,
        box_h,
        "web",
        "React + Vite su nginx, :8080; CSP restrittiva",
        fill=DEEP_TEAL,
    )
    _box(
        d,
        app_x,
        box_y,
        box_w,
        box_h,
        "app",
        "FastAPI + uvicorn, :8000; NER e docling su CPU",
        fill=DARK_TEAL,
    )
    _box(
        d,
        ollama_x,
        box_y,
        box_w,
        box_h,
        "ollama",
        "backend VLM su GPU (o vLLM, llama.cpp)",
        fill=MEDIUM_TEAL,
    )
    _arrow(d, 54, mid, web_x, mid, label="HTTP")
    _arrow(d, web_x + box_w, mid, app_x, mid, label="/api/")
    _arrow(d, app_x + box_w, mid, ollama_x, mid, label="API VLM")
    # volumes, read-only at runtime
    vol_y, vol_h = 150, 28
    for x, title, body in (
        (app_x, "volume NER + docling", "sola lettura, HF_HUB_OFFLINE=1"),
        (ollama_x, "volume modelli VLM", "catalogo di models.yaml"),
    ):
        d.add(
            Line(
                x + box_w / 2,
                vol_y + vol_h,
                x + box_w / 2,
                box_y,
                strokeColor=GRID,
                strokeWidth=1.2,
            )
        )
        _box(
            d,
            x,
            vol_y,
            box_w,
            vol_h,
            title,
            body,
            fill=TINT,
            color=DEEP_TEAL,
            size=6.8,
            stroke=GRID,
        )
    # download profile, the only one with internet access, aligned under the volumes
    _zone(d, 214, 0, right - 214, 100, "", MUTED_TEAL, dash=[3, 2])
    d.add(
        String(
            222,
            7,
            "profilo models, rete egress: solo just models, prima dell'avvio",
            fontName=FONT_B,
            fontSize=6.8,
            fillColor=MUTED_TEAL,
        )
    )
    dl_y, dl_h, inset = 24, 54, 8
    init_x, pull_x, dl_w = app_x + inset, ollama_x + inset, box_w - 2 * inset
    _box(
        d,
        init_x,
        dl_y,
        dl_w,
        dl_h,
        "models-init",
        "scarica NER, docling e ogni VLM del catalogo",
        fill=MUTED_TEAL,
    )
    _box(
        d,
        pull_x,
        dl_y,
        dl_w,
        dl_h,
        "ollama-models",
        "pull dei VLM nel volume",
        fill=MUTED_TEAL,
    )
    _box(
        d,
        0,
        dl_y,
        140,
        dl_h,
        "Internet",
        "Hugging Face, registry Ollama",
        fill=WHITE,
        color=MUTED_TEAL,
        stroke=MUTED_TEAL,
        dash=[3, 2],
    )
    _arrow(
        d,
        init_x,
        dl_y + dl_h / 2,
        140,
        dl_y + dl_h / 2,
        color=MUTED_TEAL,
        dash=[3, 2],
        label="download",
    )
    _arrow(
        d,
        init_x + dl_w,
        dl_y + dl_h / 2,
        pull_x,
        dl_y + dl_h / 2,
        color=MUTED_TEAL,
        dash=[3, 2],
        label="pull",
    )
    for x in (app_x + box_w / 2, ollama_x + box_w / 2):
        _arrow(d, x, dl_y + dl_h, x, vol_y, color=MUTED_TEAL, dash=[3, 2])
        d.add(
            String(
                x + 5,
                118,
                "scrive i modelli",
                fontName=FONT,
                fontSize=6.6,
                fillColor=MUTED_TEAL,
            )
        )
    return d


def layers_diagram() -> Drawing:
    """Modules grouped by layer, calls going downwards."""
    rows = [
        (
            "HTTP",
            [
                "main.py · create_app, lifespan",
                "middleware.py · request id, limiti",
                "errors.py · errori generici",
                "api/ · health, anonymization, jobs, catalog",
            ],
        ),
        (
            "Servizi",
            [
                "services/anonymization.py · slot, configurazione, errori",
                "services/jobs.py · coda in memoria",
                "services/results.py · ZIP e JSON",
            ],
        ),
        (
            "Pipeline",
            [
                "pipeline.py · anonymize, invariante, restore",
                "metrics.py · tempi e risorse",
            ],
        ),
        (
            "Fasi",
            [
                "ingest/",
                "extraction/",
                "detection/",
                "redaction/ · vault",
                "verification/",
            ],
        ),
        (
            "Motori",
            [
                "detectors/ · rules, labels, ner, eponyms",
                "vlm/ · Ollama, OpenAI",
                "text.py · ancoraggio",
            ],
        ),
        (
            "Config",
            [
                "config.py · ANONPIP_*",
                "pipeline_config/ · schema, livelli",
                "resources/ · YAML, prompt",
            ],
        ),
        (
            "Trasversali",
            [
                "logging_setup.py · redazione PII",
                "privacy.py · hash, maschere",
                "domain.py",
                "model_store.py · download",
            ],
        ),
    ]
    row_h, gap, label_w = 24, 5, 62
    d = Drawing(FRAME_W, len(rows) * (row_h + gap))
    fills = [DARK_TEAL, DEEP_TEAL, MEDIUM_TEAL, LIGHT_TEAL, CYAN, GREEN_TEAL, MINT]
    for i, (layer, modules) in enumerate(rows):
        y = (len(rows) - 1 - i) * (row_h + gap)
        _box(d, 0, y, label_w, row_h, layer, fill=fills[i], size=7.8)
        avail = FRAME_W - label_w - 6
        chip = (avail - (len(modules) - 1) * 4) / len(modules)
        for j, module in enumerate(modules):
            x = label_w + 6 + j * (chip + 4)
            name, _, what = module.partition(" · ")
            _box(
                d,
                x,
                y,
                chip,
                row_h,
                name,
                what,
                fill=TINT,
                color=DEEP_TEAL,
                size=7.2,
                stroke=GRID,
            )
    return d


def layers_of_configuration() -> Drawing:
    """The five configuration layers, from the weakest to the strongest."""
    layers = [
        ("default.yaml", "+ prompt, categorie del registro"),
        ("file utente", "solo le chiavi da cambiare"),
        ("file dei modelli", "catalogo VLM e modello per fase"),
        ("variabili ANONPIP_*", "backend, modalità predefinite"),
        ("richiesta", "modelli, modalità, categorie"),
    ]
    d = Drawing(FRAME_W, 70)
    gap = 12
    w = (FRAME_W - 4 * gap) / 5
    fills = [MINT, GREEN_TEAL, LIGHT_TEAL, DEEP_TEAL, DARK_TEAL]
    for i, (title, body) in enumerate(layers):
        x = i * (w + gap)
        _box(d, x, 14, w, 48, title, body, fill=fills[i], size=7.8)
        if i < 4:
            _arrow(d, x + w + 1, 38, x + w + gap - 1, 38)
    d.add(String(0, 2, "più debole", fontName=FONT, fontSize=6.8, fillColor=MUTED_TEAL))
    d.add(
        String(
            FRAME_W,
            2,
            "più forte: vince sulle precedenti",
            fontName=FONT,
            fontSize=6.8,
            fillColor=MUTED_TEAL,
            textAnchor="end",
        )
    )
    return d


# ----------------------------------------------------------------------------- pages


def _footer(canvas: Canvas, number: int, notice: bool) -> None:
    logo = ASSETS / "bvtech-logo-teal.png"
    canvas.drawImage(
        str(logo), MARGIN, 24, width=60, height=60 * 218 / 1204, mask="auto"
    )
    canvas.setFont(FONT, 8)
    canvas.setFillColor(DEEP_TEAL)
    canvas.drawRightString(PAGE_W - MARGIN - 18, 25, FOOTER_TEXT)
    canvas.setFont(FONT_B, 8)
    canvas.drawRightString(PAGE_W - MARGIN, 25, str(number))
    if notice:
        canvas.setFont(FONT, 6.5)
        canvas.setFillColor(MUTED_TEAL)
        canvas.drawCentredString(PAGE_W / 2, 12, CONFIDENTIAL)


def _first_page(canvas: Canvas, doc: BaseDocTemplate) -> None:
    canvas.saveState()
    canvas.setFillColor(DEEP_TEAL)
    canvas.rect(0, PAGE_H - BAND_H, PAGE_W, BAND_H, stroke=0, fill=1)
    canvas.setFillColor(DARK_TEAL)
    canvas.rect(0, PAGE_H - BAND_H, PAGE_W, 7, stroke=0, fill=1)
    logo = ASSETS / "bvtech-logo-payoff-white.png"
    canvas.drawImage(
        str(logo),
        MARGIN - 6,
        PAGE_H - 102,
        width=150,
        height=150 * 827 / 1654,
        mask="auto",
    )
    canvas.setFillColor(WHITE)
    canvas.setFont(FONT_B, 30)
    canvas.drawString(MARGIN, PAGE_H - 152, "Anonymization Pipeline")
    canvas.setFont(FONT, 12.5)
    canvas.drawString(
        MARGIN,
        PAGE_H - 174,
        "Documentazione tecnica: flusso logico, architettura e componenti",
    )
    canvas.setFillColor(MINT)
    canvas.setFont(FONT_B, 8.5)
    canvas.drawString(
        MARGIN,
        PAGE_H - 204,
        f"VERSIONE {FACTS['version']}   ·   {str(FACTS['date']).upper()}   ·   "
        "PIATTAFORMA MIA   ·   AGENAS",
    )
    _footer(canvas, doc.page, notice=False)
    canvas.restoreState()


def _later_page(canvas: Canvas, doc: BaseDocTemplate) -> None:
    canvas.saveState()
    canvas.setStrokeColor(CYAN)
    canvas.setLineWidth(1.2)
    canvas.line(MARGIN, PAGE_H - 34, PAGE_W - MARGIN, PAGE_H - 34)
    canvas.setFont(FONT_B, 7.5)
    canvas.setFillColor(DEEP_TEAL)
    canvas.drawString(MARGIN, PAGE_H - 28, "ANONYMIZATION PIPELINE")
    canvas.setFont(FONT, 7.5)
    canvas.setFillColor(MUTED_TEAL)
    canvas.drawRightString(
        PAGE_W - MARGIN,
        PAGE_H - 28,
        f"Documentazione tecnica · versione {FACTS['version']}",
    )
    _footer(canvas, doc.page, notice=True)
    canvas.restoreState()


def page_purpose() -> list[Flowable]:
    principles = [
        ["Principio", "Come è realizzato"],
        [
            "<b>Tutto nel perimetro</b>",
            f"VLM locale (Ollama o server compatibile OpenAI), docling e rilevatori su CPU. A runtime "
            f"nessun accesso a internet: i modelli si scaricano prima, con {c('just models')}.",
        ],
        [
            "<b>Fail-closed</b>",
            "Se un dato può restare in chiaro (residui, invariante violata, verifica non disponibile) "
            "il documento è rifiutato con HTTP 422, senza valori nella risposta.",
        ],
        [
            "<b>Modello non fidato</b>",
            "Ogni valore proposto deve comparire nel testo (ancoraggio), gli altri sono "
            "allucinazioni e si scartano. Un'istruzione nascosta nel documento può al più "
            "nascondere un residuo, mai far comparire un valore.",
        ],
        [
            "<b>Rilevatori in unione</b>",
            "VLM, regole con checksum, etichette e NER lavorano in parallelo: basta un rilevatore "
            "per censurare un valore.",
        ],
        [
            "<b>Sostituzione meccanica</b>",
            "Il modello decide cosa è sensibile e quali varianti sono la stessa entità; il codice "
            "sostituisce ogni forma in tutto il documento con tag coerenti ("
            + c("[PERSONA_1]")
            + "), quindi il ripristino è esatto.",
        ],
        [
            "<b>Nessun dato nei log</b>",
            "Report, metriche ed eventi contengono solo conteggi, categorie, pagine e tempi; i "
            "codici fiscali sono mascherati anche nei traceback.",
        ],
        [
            "<b>Configurazione dichiarativa</b>",
            "Pipeline, categorie, prompt, modelli e vocabolari dei rilevatori sono file YAML "
            "validati all'avvio: una chiave sconosciuta ferma il servizio.",
        ],
    ]
    return [
        h1(1, "Scopo e principi"),
        p(
            "<b>Anonymization Pipeline</b> è il servizio della piattaforma MIA che anonimizza "
            "documenti italiani, soprattutto sanitari: PDF nativi e scansionati, immagini e testo. "
            "Restituisce il Markdown del documento censurato, un report senza valori e, in "
            "modalità pseudonimo, una mappa cifrata che consente di ripristinare i valori "
            "originali solo a chi possiede la chiave."
        ),
        Spacer(1, 5),
        p(
            "Il servizio è stateless (nessun database, lavori asincroni in memoria) e deroga "
            "al template AGENAS in un solo punto sostanziale: non usa llm-gateway, perché il VLM "
            "è locale e i documenti non devono uscire dal perimetro "
            f"({c('docs/DEPLOY_HANDOVER.md')})."
        ),
        Spacer(1, 10),
        Kpis(
            [
                ("5", "fasi della pipeline"),
                (
                    str(FACTS["categories"]),
                    f"categorie di dati personali in {FACTS['groups']} gruppi",
                ),
                ("4", "rilevatori in unione"),
                ("0", "chiamate verso l'esterno a runtime"),
            ]
        ),
        Paragraph("Principi di progetto", H2),
        table(principles, [118, FRAME_W - 118]),
        Paragraph("Stack tecnologico", H2),
        table(
            [
                ["Livello", "Tecnologie"],
                [
                    "<b>Backend</b>",
                    "Python 3.12, FastAPI e uvicorn, Pydantic e pydantic-settings, "
                    "httpx, pypdfium2 e Pillow, cryptography (AES-256-GCM, scrypt); uv, ruff, ty, pytest",
                ],
                [
                    "<b>Modelli</b>",
                    "VLM su Ollama o server compatibile OpenAI (GPU); docling e NER "
                    "Hugging Face con transformers e torch su CPU (extra opzionali)",
                ],
                [
                    "<b>Interfaccia web</b>",
                    "React, TypeScript e Vite, servita da nginx con CSP "
                    "restrittiva; pdf.js, IndexedDB",
                ],
            ],
            [118, FRAME_W - 118],
        ),
        PageBreak(),
    ]


def page_flow() -> list[Flowable]:
    rows = [
        ["Fase", "Cosa fa", "Garanzie"],
        [
            f"<b>Ingest</b><br/>{c('ingest/')}",
            "Riconosce il formato dai magic byte, renderizza le pagine PDF (200 dpi) e decide "
            "se una pagina è nativa: testo leggibile e visibile, poche immagini, niente testo "
            "nascosto. Il testo semplice è diviso in blocchi.",
            "Limiti su pagine e pixel (decompression bomb); file cifrati o corrotti: HTTP 400 "
            "con messaggio generico.",
        ],
        [
            f"<b>Estrazione</b><br/>{c('extraction/')}",
            "Pagine native con docling (testo esatto, tabelle), in parallelo con il VLM che "
            "trascrive scansioni e immagini in Markdown fedele, una chiamata per pagina.",
            "Senza docling ogni pagina va al VLM, con un avviso; trascrizione troncata: avviso "
            "nel report.",
        ],
        [
            f"<b>Rilevamento</b><br/>{c('detection/')}<br/>{c('detectors/')}",
            "VLM (3 passate di categorie per pagina, risposta vincolata da JSON schema), regole "
            'con checksum (CF, IBAN, P.IVA, carte, ICD-10), etichette ("Paziente: ...") e NER, '
            "in unione.",
            "Ancoraggio letterale o approssimato; categoria spenta: ripiego sulla generica; "
            "eponimi e scale scartati solo nel loro contesto medico.",
        ],
        [
            f"<b>Censura</b><br/>{c('redaction/')}",
            "Il VLM raggruppa le varianti della stessa entità (coreferenza); il codice sostituisce "
            "ogni forma in tutto il documento. Modalità pseudonym (tag e mappa), tag o redaction "
            "(simbolo fisso).",
            "Varianti fuse solo nella stessa famiglia; mappa AES-256-GCM, chiave casuale o "
            "passphrase con scrypt, intestazione autenticata.",
        ],
        [
            f"<b>Verifica</b><br/>{c('verification/')}",
            "Il VLM rilegge ogni pagina censurata confrontandola con il Markdown originale e "
            "segnala i dati rimasti in chiaro. Poi il controllo dell'invariante sul testo finale.",
            "Modalità off, report o block (default); verifica obbligatoria: se il VLM non "
            "risponde il documento è rifiutato.",
        ],
    ]
    return [
        h1(2, "Flusso logico"),
        p(
            "Ogni documento attraversa cinque fasi in sequenza ("
            + c("pipeline.py")
            + "). Il "
            "VLM decide cosa è scritto nelle pagine non native, cosa è sensibile, quali valori "
            "sono la stessa entità e cosa è sfuggito; il codice fa I/O, valida l'output dei "
            "rilevatori e sostituisce le stringhe. Ogni fase usa il proprio modello, scelto nel "
            f"file dei modelli ({escape(str(FACTS['models']))})."
        ),
        Spacer(1, 8),
        flow_diagram(),
        Spacer(1, 10),
        table(rows, [72, 232, FRAME_W - 304]),
        Paragraph("Esiti e ripristino", H2),
        p(
            f"{c('POST /anonymize')} restituisce uno ZIP (default) o JSON con "
            f"{c('<nome>.anon.md')}, {c('.report.json')}, {c('.metriche.json')} e, in modalità "
            f"pseudonimo, {c('.mappa.enc')}. {c('POST /restore')} ricostruisce il documento "
            f"originale da Markdown censurato, mappa e chiave ({c('/restore/detail')} con le "
            "posizioni dei tag). Chiave errata o mappa alterata: HTTP 403."
        ),
        PageBreak(),
    ]


def page_architecture() -> list[Flowable]:
    runtime = [
        ["", "Richiesta sincrona", "Lavoro asincrono"],
        [
            "<b>Endpoint</b>",
            c("POST /anonymize"),
            f"{c('POST /jobs')}, poi {c('GET /jobs/{id}')} e {c('GET /jobs/{id}/result')}",
        ],
        [
            "<b>Concorrenza</b>",
            f"attende uno slot fino a {c('ANONPIP_JOB_WAIT_S')} (30 s), poi HTTP 503 con "
            "Retry-After",
            f"coda in memoria (max {c('ANONPIP_MAX_QUEUED_JOBS')}, 20), stessi slot delle "
            "richieste sincrone",
        ],
        [
            "<b>Stato</b>",
            "nessuno: la risposta contiene il risultato",
            "fase corrente e chiamate VLM; risultato fino alla scadenza (1 ora) o alla "
            "cancellazione",
        ],
        ["<b>Uso</b>", "integrazione fra servizi, script", "interfaccia web"],
    ]
    return [
        h1(3, "Vista architetturale"),
        p(
            "Tre container in esecuzione su due reti; un profilo separato, l'unico con accesso a "
            "internet, scarica i modelli prima dell'avvio. Nel cluster le stesse regole sono "
            f"NetworkPolicy ({c('docs/k8s-example/service.yaml')})."
        ),
        Spacer(1, 8),
        deployment_diagram(),
        Paragraph("Isolamento e deploy", H2),
        table(
            [
                ["Compose (sviluppo)", "Kubernetes (staging e produzione)"],
                [
                    f"{c('ollama')} solo sulla rete internal, senza porte pubblicate; {c('app')} "
                    f"raggiunge solo URL interni, legge i modelli con {c('HF_HUB_OFFLINE=1')} e non "
                    f"chiede mai download a Ollama ({c('ANONPIP_VLM_PULL_MISSING=false')}); porte su "
                    "127.0.0.1.",
                    "Immagini API e web su Harbor, Flux CD. Esposizione interna (traefik-int / WSO2), "
                    f"probe {c('/healthz')} e {c('/readyz')} (backend VLM e modello di ogni fase), "
                    "1 replica, backend VLM con 1 GPU NVIDIA, PVC dei modelli in sola lettura, "
                    "nessun database.",
                ],
            ],
            [FRAME_W / 2, FRAME_W / 2],
        ),
        Paragraph("Vista runtime", H2),
        p(
            "La GPU è il collo di bottiglia: i documenti elaborati insieme sono limitati da "
            f"{c('ANONPIP_MAX_CONCURRENT_JOBS')} (1). Una sola replica e un solo worker uvicorn, "
            "perché i lavori asincroni vivono nella memoria del processo; i byte del file e la "
            "chiave sono rilasciati a fine elaborazione."
        ),
        Spacer(1, 5),
        table(runtime, [72, (FRAME_W - 72) / 2, (FRAME_W - 72) / 2]),
        PageBreak(),
    ]


def page_components() -> list[Flowable]:
    rows = [
        ["Componente", "Responsabilità"],
        [
            c("api/"),
            "Router sottili: validano i campi (modelli Pydantic con limiti) e delegano ai "
            f"servizi. {c('/anonymize')}, {c('/restore')}, {c('/jobs')}, {c('/models')}, "
            f"{c('/categories')}, {c('/configuration')}, probe.",
        ],
        [
            c("services/anonymization.py"),
            "Configurazione della richiesta, slot di concorrenza, "
            "mappatura degli errori su HTTP, stato per /readyz; all'avvio valida configurazione e "
            "file dei rilevatori.",
        ],
        [
            c("services/jobs.py"),
            "Coda in memoria, worker, avanzamento per fase, scadenza dei "
            "risultati; eventi job_queued e job_finished.",
        ],
        [
            c("pipeline.py"),
            "Orchestrazione delle fasi, controllo dell'invariante, unione delle "
            "pagine, report, cifratura della mappa, ripristino.",
        ],
        [
            c("detectors/"),
            "Rilevatori di testo indipendenti: rules (regex e checksum), labels "
            "(etichetta e valore), ner (modello Hugging Face su CPU), eponyms (filtro contestuale); "
            f"{c('patterns.py')}: sintassi comune delle regex.",
        ],
        [
            c("vlm/"),
            "Client per Ollama (API nativa, num_ctx) e per server compatibili OpenAI; "
            "codifica delle immagini, retry, parsing tollerante delle risposte JSON.",
        ],
        [
            c("text.py"),
            "Ancoraggio dei valori al Markdown (letterale, case-insensitive, "
            "approssimato) e riconoscimento dei tag già inseriti.",
        ],
        [
            c("pipeline_config/"),
            "Schema Pydantic della pipeline, caricamento a livelli, registro "
            "delle categorie, catalogo dei modelli.",
        ],
        [
            c("logging_setup.py, privacy.py"),
            "Formato dei log per Loki, filtro che maschera i "
            "codici fiscali, hash e maschere degli identificativi, eventi KPI.",
        ],
        [
            c("model_store.py"),
            "Download di VLM, NER e docling prima dell'avvio: l'unico passo "
            "con internet.",
        ],
    ]
    pages = [
        ["Pagina web", "Funzione"],
        [
            "Carica / Stato",
            "Invio del documento con modalità, modelli per fase, rilevatori e "
            "categorie; stato del servizio e dei modelli.",
        ],
        [
            "Confronto",
            "Originale, Markdown in chiaro e censurato affiancati, con i tag "
            "evidenziati.",
        ],
        [
            "Dashboard / Storico",
            "Tempi, chiamate VLM e conteggi; storico in IndexedDB con soli "
            "report e metriche, mai il nome del file.",
        ],
        [
            "Ripristino",
            "Markdown e mappa con la chiave, che resta solo in sessionStorage.",
        ],
    ]
    return [
        h1(4, "Componenti"),
        p(
            f"Il backend segue il layout del template: {c('api/')} (router) {ARROW} "
            f"{c('services/')} (logica) {ARROW} {c('pipeline.py')} {ARROW} moduli di fase, che "
            "usano i motori (rilevatori, client VLM, ancoraggio) e la configurazione. Le "
            "dipendenze scendono soltanto; i rilevatori non si conoscono fra loro."
        ),
        Spacer(1, 8),
        layers_diagram(),
        Spacer(1, 8),
        table(rows, [118, FRAME_W - 118]),
        Spacer(1, 7),
        table(pages, [118, FRAME_W - 118]),
        PageBreak(),
    ]


def page_operations() -> list[Flowable]:
    resources = [
        ["File in resources/", "Contenuto"],
        [
            c("default.yaml"),
            "Parametri regolabili: backend, generazione, immagini, fasi, "
            "rilevatori accesi, soglie, output.",
        ],
        [
            c("models.yaml"),
            "Catalogo dei VLM (contesto, visione) e modello predefinito di ogni "
            "fase; sostituibile con un file proprio.",
        ],
        [
            c("categories.yaml"),
            f"Registro delle {FACTS['categories']} categorie, gruppi, "
            "famiglie generiche, coreferenza libera.",
        ],
        [
            c("prompts/<fase>.yaml"),
            "Prompt di sistema e utente, con i segnaposto validati.",
        ],
        [
            c("detectors/*.yaml"),
            "Vocabolari dei rilevatori (regole, etichette, NER, eponimi): "
            "validati all'avvio, non sovrascrivibili dal file utente.",
        ],
    ]
    controls = [
        ["Privacy e sicurezza", "Osservabilità e operatività"],
        [
            "Log in chiaro solo con conteggi e hash; testo dell'utente solo in DEBUG fuori "
            "produzione e redatto; codici fiscali mascherati da un filtro su tutto lo stdout. "
            "Nessun dato personale in URL. Errori generici in italiano, mai stack trace o input. "
            "Limite di upload (413/411), X-Request-ID validato, OpenAPI e trascrizione in chiaro "
            "disattivate in produzione. nginx: CSP, X-Frame-Options DENY, no-referrer.",
            "Eventi document_anonymized, document_rejected, job_queued e job_finished verso "
            "Loki (contratto con KPI-dashboard). Metriche per documento: tempi per fase e "
            f"pagina, latenze e token del VLM, CPU e memoria. {c('/readyz')} riporta backend VLM, "
            "modelli, NER e docling.",
        ],
    ]
    codes = [
        [
            "Risposta",
            "Quando (messaggio generico in italiano, mai valori del documento)",
        ],
        [
            "<b>400</b>",
            "Documento non valido, cifrato o in formato non supportato; parametro "
            "non valido (modello fuori catalogo, categoria sconosciuta).",
        ],
        [
            "<b>403</b> / <b>404</b> / <b>409</b>",
            "Ripristino non riuscito (chiave o mappa); lavoro "
            "inesistente o scaduto; risultato richiesto prima della fine del lavoro.",
        ],
        [
            "<b>411</b> / <b>413</b>",
            "Upload senza lunghezza o oltre ANONPIP_MAX_UPLOAD_MB.",
        ],
        [
            "<b>422</b>",
            "Rifiuto fail-closed: residui, invariante violata, verifica non "
            "disponibile; nel corpo solo categorie e pagine.",
        ],
        [
            "<b>503</b>",
            "Backend VLM o modello non disponibile; servizio occupato (con "
            "Retry-After).",
        ],
    ]
    commands = [
        ["Comando", "Effetto"],
        [c("just models"), "Scarica VLM, NER e docling (unico passo con internet)."],
        [
            f"{c('just up-gpu')} / {c('just up')}",
            "Avvia lo stack compose su GPU o su CPU.",
        ],
        [
            f"{c('just lint')} / {c('just test-cov')}",
            "ruff e ty; unit test con coverage minima 80%.",
        ],
        [c("just test-vlm"), "Test di integrazione contro l'Ollama del compose."],
        [c("just docs-pdf"), "Rigenera questo documento."],
    ]
    return [
        h1(5, "Configurazione, sicurezza, operatività"),
        p(
            "La configurazione effettiva è la fusione di cinque livelli: il file utente è indicato "
            f"da {c('ANONPIP_PIPELINE_CONFIG_FILE')}, il file dei modelli da "
            f"{c('ANONPIP_MODELS_FILE')}. Ogni chiave sconosciuta è un errore e le chiavi possedute "
            "dalle variabili o dal file dei modelli sono rifiutate nel file utente. "
            f"{c('GET /configuration')} mostra la configurazione effettiva senza segreti."
        ),
        Spacer(1, 6),
        layers_of_configuration(),
        Spacer(1, 8),
        table(resources, [118, FRAME_W - 118]),
        Spacer(1, 8),
        table(controls, [FRAME_W / 2, FRAME_W / 2]),
        Spacer(1, 8),
        table(codes, [118, FRAME_W - 118]),
        Spacer(1, 8),
        table(commands, [118, FRAME_W - 118]),
    ]


class _Document(BaseDocTemplate):
    """Two page templates: the first with the cover band, the others with the header rule."""

    def __init__(self, path: Path) -> None:
        super().__init__(
            str(path),
            pagesize=A4,
            leftMargin=MARGIN,
            rightMargin=MARGIN,
            topMargin=52,
            bottomMargin=48,
            title="Anonymization Pipeline - Documentazione tecnica",
            author="BV TECH S.p.A.",
            subject="Flusso logico, architettura e componenti",
            creator="scripts/docs/architettura.py",
        )
        first = Frame(
            MARGIN,
            48,
            FRAME_W,
            PAGE_H - BAND_H - 18 - 48,
            id="first",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        later = Frame(
            MARGIN,
            48,
            FRAME_W,
            PAGE_H - 52 - 48,
            id="later",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        self.addPageTemplates(
            [
                PageTemplate("first", [first], onPage=_first_page),
                PageTemplate("later", [later], onPage=_later_page),
            ]
        )


def build(path: Path = OUTPUT) -> int:
    """Write the PDF and return its number of pages.

    Raises:
        SystemExit: If the document is longer than ``MAX_PAGES``.
    """
    doc = _Document(path)
    story: list[Flowable] = [NextPageTemplate("later")]
    for section in (
        page_purpose,
        page_flow,
        page_architecture,
        page_components,
        page_operations,
    ):
        story += section()
    doc.build(story)
    if doc.page > MAX_PAGES:
        raise SystemExit(f"{path.name}: {doc.page} pages, at most {MAX_PAGES} allowed")
    return doc.page


if __name__ == "__main__":
    build()
