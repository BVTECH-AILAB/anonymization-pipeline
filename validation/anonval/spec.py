"""Document spec (the output of the generation prompt) and resolution of its PII markers.

A spec describes one synthetic document: style, letterhead, footer and a list of layout blocks.
Personal data are written inline as ``[[CATEGORY:value]]``; checksummed identifiers are computed
here (``[[CODICE_FISCALE:@person]]``, ``[[IBAN:@label]]``...). ``resolve`` turns the markers
into plain text and returns every mention, which becomes the ground truth.
"""

import datetime
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from anonval.synthetic import (
    BIRTH_PLACES,
    codice_fiscale,
    iban,
    partita_iva,
    tessera_sanitaria,
)
from anonymization_pipeline.pipeline_config.categories import registry

DocType = Literal[
    "referto_laboratorio",
    "test_genetico",
    "biopsia_patologica",
    "lettera_dimissione",
    "nota_ambulatoriale",
    "autorizzazione_preventiva",
    "referto_radiologico",
    "referto_esterno",
]
Difficulty = Literal["facile", "media", "complessa"]
DOC_TYPES: tuple[str, ...] = DocType.__args__
DIFFICULTIES: tuple[str, ...] = Difficulty.__args__
Align = Literal["left", "center", "right"]

MARKER = re.compile(r"\[\[([A-Z_]+):([^\[\]]+?)\]\]")
COMPUTED = ("CODICE_FISCALE", "IBAN", "PARTITA_IVA", "TESSERA_SANITARIA")
# characters outside cp1252 (the encoding of the standard PDF fonts) that have a plain spelling
_SYMBOLS = {
    "≤": "<=",
    "≥": ">=",
    "→": "->",
    "←": "<-",
    "≠": "!=",
    "−": "-",
    "‐": "-",
    "‑": "-",
    " ": " ",
    " ": " ",
    " ": " ",
    "α": "alfa",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "κ": "kappa",
    "λ": "lambda",
    "μ": "µ",
    "Δ": "delta",
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Person(_Model):
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(min_length=1, max_length=60)
    sex: Literal["M", "F"]
    birth_date: datetime.date
    birth_place: str

    @field_validator("birth_place")
    @classmethod
    def _known_place(cls, value: str) -> str:
        if value not in BIRTH_PLACES:
            raise ValueError(f"birth_place must be one of {', '.join(BIRTH_PLACES)}")
        return value


class Style(_Model):
    font: Literal["helvetica", "times", "courier", "dejavu"] = "helvetica"
    color: str = Field(default="#1F4E79", pattern=r"^#[0-9A-Fa-f]{6}$")
    font_size: float = Field(default=9.5, ge=7, le=12)
    margins_mm: float = Field(default=18, ge=10, le=30)
    fax_line: str | None = None


class Letterhead(_Model):
    logo: str | None = Field(default=None, max_length=6)
    logo_shape: Literal["circle", "square", "shield", "cross", "leaf"] = "circle"
    lines: list[str] = Field(min_length=1, max_length=6)
    right: list[str] = Field(default=[], max_length=6)


class Title(_Model):
    type: Literal["title"]
    text: str
    subtitle: str | None = None


class PatientBox(_Model):
    type: Literal["patient_box"]
    title: str = "DATI DEL PAZIENTE"
    fields: list[tuple[str, str]] = Field(min_length=1)
    columns: int = Field(default=1, ge=1, le=2)
    align: Literal["right", "full"] = "full"


class Fields(_Model):
    type: Literal["fields"]
    fields: list[tuple[str, str]] = Field(min_length=1)
    columns: int = Field(default=2, ge=1, le=3)


class Section(_Model):
    type: Literal["section"]
    text: str


class Heading(_Model):
    type: Literal["heading"]
    text: str


class Paragraph(_Model):
    type: Literal["paragraph"]
    text: str
    size: float | None = Field(default=None, ge=6, le=14)
    align: Literal["justify", "left", "center", "right"] = "justify"


class Table(_Model):
    type: Literal["table"]
    header: list[str] = Field(min_length=1, max_length=10)
    rows: list[list[str]] = Field(min_length=1)
    widths: list[float] | None = None
    flag_column: int | None = None
    zebra: bool = True
    font_size: float | None = Field(default=None, ge=6, le=12)
    caption: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> "Table":
        n = len(self.header)
        if any(len(row) != n for row in self.rows):
            raise ValueError("every row must have as many cells as the header")
        if self.widths is not None and len(self.widths) != n:
            raise ValueError("widths must have as many values as the header")
        if self.flag_column is not None and not 0 <= self.flag_column < n:
            raise ValueError("flag_column out of range")
        return self


class Barcode(_Model):
    type: Literal["barcode"]
    value: str
    kind: Literal["code128", "qr"] = "code128"
    label: str | None = None
    align: Align = "left"


class CharBoxes(_Model):
    type: Literal["char_boxes"]
    label: str
    value: str


class Checkboxes(_Model):
    type: Literal["checkboxes"]
    label: str | None = None
    items: list[tuple[str, bool]] = Field(min_length=1)
    columns: int = Field(default=2, ge=1, le=3)


class Signature(_Model):
    type: Literal["signature"]
    place_date: str | None = None
    role: str | None = None
    name: str | None = None
    extra: list[str] = []
    digital: bool = False
    stamp: list[str] | None = None


class Stamp(_Model):
    type: Literal["stamp"]
    lines: list[str] = Field(min_length=1, max_length=6)
    angle: float = Field(default=-6, ge=-20, le=20)
    align: Align = "right"


class Note(_Model):
    type: Literal["note"]
    text: str


class Footnotes(_Model):
    type: Literal["footnotes"]
    items: list[str] = Field(min_length=1)


class Address(_Model):
    type: Literal["address"]
    lines: list[str] = Field(min_length=1)
    align: Literal["left", "right"] = "right"


class PageBreak(_Model):
    type: Literal["page_break"]


class Spacer(_Model):
    type: Literal["spacer"]
    mm: float = Field(default=4, ge=1, le=60)


class Columns(_Model):
    type: Literal["columns"]
    left: list["Block"] = Field(min_length=1)
    right: list["Block"] = Field(min_length=1)
    divider: bool = True


Block = Annotated[
    Title
    | PatientBox
    | Fields
    | Section
    | Heading
    | Paragraph
    | Table
    | Barcode
    | CharBoxes
    | Checkboxes
    | Signature
    | Stamp
    | Note
    | Footnotes
    | Address
    | PageBreak
    | Spacer
    | Columns,
    Field(discriminator="type"),
]
Columns.model_rebuild()


class Negative(_Model):
    value: str = Field(min_length=2)
    forbidden_categories: list[str] = Field(min_length=1)


class Spec(_Model):
    id: str = Field(pattern=r"^[a-z_]+_(facile|media|complessa)_\d{2}$")
    type: DocType
    difficulty: Difficulty
    style: Style = Style()
    persons: dict[str, Person] = {}
    letterhead: Letterhead
    running_header: str | None = None
    footer: list[str] = Field(default=[], max_length=4)
    blocks: list[Block] = Field(min_length=1)
    negatives: list[Negative] = []

    @model_validator(mode="after")
    def _id_prefix(self) -> "Spec":
        if not self.id.startswith(f"{self.type}_{self.difficulty}_"):
            raise ValueError("id must be <type>_<difficulty>_<nn>")
        return self


@dataclass(frozen=True)
class Mention:
    """One marker of the spec: a ground truth value and where it sits in the layout."""

    value: str
    category: str
    location: str


def sanitize(text: str) -> str:
    """Plain spelling for symbols outside cp1252.

    Raises:
        ValueError: A character the standard PDF fonts cannot draw is left.
    """
    out = "".join(_SYMBOLS.get(c, c) for c in text)
    bad = sorted({c for c in out if not _cp1252(c)})
    if bad:
        raise ValueError(f"characters not allowed: {' '.join(map(repr, bad))}")
    return out


def _cp1252(char: str) -> bool:
    try:
        char.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


class _Resolver:
    def __init__(self, spec: Spec) -> None:
        self.spec = spec
        self.categories = set(registry().descriptions)
        self.labels: dict[tuple[str, str], str] = {}
        self.mentions: list[Mention] = []

    def computed(self, category: str, label: str) -> str:
        if category not in COMPUTED:
            raise ValueError(
                f"{self.spec.id}: @{label} is allowed only for {', '.join(COMPUTED)}, not {category}"
            )
        key = (category, label)
        if key not in self.labels:
            seed = f"{self.spec.id}:{category}:{label}"
            if category == "CODICE_FISCALE":
                person = self.spec.persons.get(label)
                if person is None:
                    raise ValueError(f"{self.spec.id}: unknown person @{label}")
                value = codice_fiscale(
                    person.first_name,
                    person.last_name,
                    person.sex,
                    person.birth_date.isoformat(),
                    person.birth_place,
                )
            elif category == "IBAN":
                value = iban(seed=seed)
            elif category == "PARTITA_IVA":
                value = partita_iva(seed=seed)
            else:
                value = tessera_sanitaria(seed=seed)
            self.labels[key] = value
        return self.labels[key]

    def text(self, text: str, location: str) -> str:
        def replace(match: re.Match[str]) -> str:
            category, value = match.group(1), match.group(2).strip()
            if category not in self.categories:
                raise ValueError(f"{self.spec.id}: unknown category {category}")
            if value.startswith("@"):
                value = self.computed(category, value[1:])
            value = sanitize(value)
            self.mentions.append(Mention(value, category, location))
            return value

        out = MARKER.sub(replace, text)
        if "[[" in out or "]]" in out:
            raise ValueError(f"{self.spec.id}: malformed marker in {text!r}")
        try:
            return sanitize(out)
        except ValueError as exc:
            raise ValueError(f"{self.spec.id}: {exc} in {text!r}") from exc

    def walk(self, node: Any, location: str) -> Any:
        if isinstance(node, str):
            return self.text(node, location)
        if isinstance(node, list | tuple):
            return [self.walk(item, location) for item in node]
        if isinstance(node, dict):
            here = node.get("type", location)
            return {
                key: self.walk(value, "stamp" if key == "stamp" else here)
                for key, value in node.items()
            }
        return node


_TOP_LOCATIONS = {
    "letterhead": "letterhead",
    "running_header": "running_header",
    "footer": "footer",
    "blocks": "body",
}


def resolve(spec: Spec) -> tuple[Spec, list[Mention]]:
    """Spec with plain text in place of the markers, and the mentions in document order.

    Raises:
        ValueError: Unknown category or person, computed value not allowed, malformed marker,
            character the PDF fonts cannot draw.
    """
    resolver = _Resolver(spec)
    data = spec.model_dump(mode="json")
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key == "style":
            fax = value.get("fax_line")
            value = {
                **value,
                "fax_line": resolver.text(fax, "fax_line") if fax else None,
            }
        elif key in _TOP_LOCATIONS:
            value = resolver.walk(value, _TOP_LOCATIONS[key])
        out[key] = value
    for negative in spec.negatives:
        for category in negative.forbidden_categories:
            if category not in resolver.categories:
                raise ValueError(f"{spec.id}: unknown category {category} in negatives")
    return Spec.model_validate(out), resolver.mentions
