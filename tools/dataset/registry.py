"""Ground-truth registry for synthetic documents.

Every PII mention is drawn in a colour that encodes its id: each RGB channel is
offset by at most 15/255 from pure black (or pure white for text on dark
bands), which is invisible to the eye but readable back from the PDF spans.
Non-PII text must never use colours inside those ranges.

Images (signatures, stamps, codes) are registered by their unique pixel size.
"""

from dataclasses import asdict, dataclass, field
from xml.sax.saxutils import escape

from reportlab.lib.colors import Color

from anonmyzation_pipeline.services.anonymization.taxonomy import LABELS

MAX_ID = 4095


@dataclass
class Mention:
    id: int
    text: str
    label: str
    required: bool
    visible: bool = True
    kind: str = "text"  # text | image | metadata | annotation
    boxes: list[dict] = field(
        default_factory=list
    )  # {"page": int, "rect": [x0, y0, x1, y1]}

    def to_dict(self) -> dict:
        return asdict(self)


def id_to_rgb(mid: int, base: str = "black") -> tuple[int, int, int]:
    r, g, b = (mid >> 8) & 15, (mid >> 4) & 15, mid & 15
    if base == "white":
        return 255 - r, 255 - g, 255 - b
    return r, g, b


def rgb_to_id(rgb: int) -> int | None:
    r, g, b = (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255
    if max(r, g, b) <= 15:
        mid = (r << 8) | (g << 4) | b
    elif min(r, g, b) >= 240:
        mid = ((255 - r) << 8) | ((255 - g) << 4) | (255 - b)
    else:
        return None
    return mid or None


class Registry:
    def __init__(self, clean: bool = False):
        # clean=True: same mentions and ids, but pure colours (no hidden signal in the PDF).
        self.clean = clean
        self.mentions: list[Mention] = []
        self._img_sizes: dict[tuple[int, int], int] = {}

    def _new(
        self, text: str, label: str, required: bool | None, visible: bool, kind: str
    ) -> Mention:
        if label not in LABELS:
            raise KeyError(label)
        if len(self.mentions) >= MAX_ID:
            raise RuntimeError("too many mentions")
        m = Mention(
            id=len(self.mentions) + 1,
            text=text,
            label=label,
            required=LABELS[label].redact if required is None else required,
            visible=visible,
            kind=kind,
        )
        self.mentions.append(m)
        return m

    # -- text ---------------------------------------------------------------
    def mark(
        self,
        text: str,
        label: str,
        base: str = "black",
        required: bool | None = None,
        visible: bool = True,
    ) -> str:
        """Paragraph markup for a PII mention."""
        m = self._new(text, label, required, visible, "text")
        r, g, b = id_to_rgb(0 if self.clean else m.id, base)
        return f'<font color="#{r:02x}{g:02x}{b:02x}">{escape(text)}</font>'

    def color(
        self,
        text: str,
        label: str,
        base: str = "black",
        required: bool | None = None,
        visible: bool = True,
    ) -> Color:
        """Canvas fill colour for a PII mention drawn with drawString."""
        m = self._new(text, label, required, visible, "text")
        r, g, b = id_to_rgb(0 if self.clean else m.id, base)
        return Color(r / 255, g / 255, b / 255)

    # -- images -------------------------------------------------------------
    def image(self, label: str, w: int, h: int, note: str = "") -> tuple[int, int]:
        """Register an image; returns a pixel size unique within the document."""
        while (w, h) in self._img_sizes:
            w += 1
        m = self._new(note or label, label, None, True, "image")
        self._img_sizes[(w, h)] = m.id
        return w, h

    def image_id(self, w: int, h: int) -> int | None:
        return self._img_sizes.get((w, h))

    # -- non-page leaks -------------------------------------------------------
    def hidden(self, text: str, label: str, kind: str) -> None:
        """PII that lives outside page content (metadata, annotation history)."""
        self._new(text, label, None, False, kind)

    def by_id(self, mid: int) -> Mention | None:
        return self.mentions[mid - 1] if 0 < mid <= len(self.mentions) else None
