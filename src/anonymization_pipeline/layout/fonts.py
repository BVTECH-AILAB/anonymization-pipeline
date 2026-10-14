"""Catalog of the fonts of the layout PDF (``resources/fonts.yaml``) and resolution of PDF fonts.

A font of the original is never embedded: its name and flags choose a family of the catalog
(``resolve``), and characters the family lacks are drawn with the fallback family (``segments``).
"""

import functools
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Literal, cast, get_args

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from anonymization_pipeline.layout.model import FontKey
from anonymization_pipeline.pipeline_config.resources import read_yaml

FILE = "fonts.yaml"
_FOLDER = "fonts"
_STYLES = ("regular", "bold", "italic", "bolditalic")
_GENERIC = ("sans", "serif", "mono")
_KEYS = ("families", "aliases", "generic", "glyph_fallback")
# PDF font descriptor flags (ISO 32000-1, 9.8.2)
FIXED_PITCH, SERIF, ITALIC, FORCE_BOLD = 1, 2, 64, 1 << 18
_SUBSET = re.compile(r"^[A-Z]{6}\+")
_SUFFIX = re.compile(r"(mt|ps)+$")
_STYLE_WORDS = re.compile(
    r"(bold|italic|oblique|regular|book|medium|semibold|demibold|demi|light|black|heavy"
    r"|condensed|narrow)+$"
)
_BOLD = re.compile(r"bold|black|heavy|demi")
_ITALIC = re.compile(r"italic|oblique")

Resolution = Literal["alias", "generic"]


@dataclass(frozen=True)
class Catalog:
    """Content of ``fonts.yaml``, validated."""

    families: dict[str, dict[str, str]]
    aliases: dict[str, str]
    generic: dict[str, str]
    glyph_fallback: str


@dataclass(frozen=True)
class Resolved:
    """Catalog family and style chosen for a PDF font, and how it was chosen."""

    font: FontKey
    bold: bool
    italic: bool
    how: Resolution


def _mapping(data: Any, keys: tuple[str, ...], where: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"{where}: expected a mapping")
    if missing := [k for k in keys if k not in data]:
        raise ValueError(f"{where}: missing keys {', '.join(missing)}")
    if unknown := sorted(set(map(str, data)) - set(keys)):
        raise ValueError(f"{where}: unknown keys {', '.join(unknown)}")
    return data


@functools.cache
def catalog() -> Catalog:
    """The font catalog (read at first use, then cached).

    Raises:
        ValueError: On a missing or unknown key, a family other than the model's, a missing font
            file or an alias listed twice.
    """
    data = _mapping(read_yaml(FILE), _KEYS, FILE)
    known = set(get_args(FontKey))
    families = _mapping(data["families"], tuple(sorted(known)), f"{FILE}, families")
    folder = files("anonymization_pipeline.resources").joinpath(_FOLDER)
    for family, styles in families.items():
        _mapping(styles, _STYLES, f"{FILE}, families.{family}")
        for name in styles.values():
            if not isinstance(name, str) or not folder.joinpath(name).is_file():
                raise ValueError(f"{FILE}, families.{family}: missing file {name}")
    aliases: dict[str, str] = {}
    if not isinstance(data["aliases"], dict):
        raise ValueError(f"{FILE}, aliases: expected a mapping")
    for family, names in data["aliases"].items():
        if family not in known or not isinstance(names, list):
            raise ValueError(f"{FILE}, aliases.{family}: unknown family or not a list")
        for name in names:
            if not isinstance(name, str) or normalize(name) != name:
                raise ValueError(
                    f"{FILE}, aliases.{family}: '{name}' is not normalized"
                )
            if name in aliases:
                raise ValueError(f"{FILE}: alias '{name}' listed twice")
            aliases[name] = family
    generic = _mapping(data["generic"], _GENERIC, f"{FILE}, generic")
    fallback = data["glyph_fallback"]
    if not set(generic.values()) | {fallback} <= known:
        raise ValueError(f"{FILE}: generic and glyph_fallback must be catalog families")
    return Catalog(families, aliases, dict(generic), fallback)


def normalize(name: str) -> str:
    """Family part of a PDF font name: ``ABCDEF+TimesNewRomanPS-BoldItalicMT`` -> ``timesnewroman``."""
    base = _SUBSET.sub("", name)
    base = re.split(r"[-,]", base, maxsplit=1)[0].lower()
    base = re.sub(r"[^a-z0-9]", "", base)
    for pattern in (_SUFFIX, _STYLE_WORDS, _SUFFIX):
        stripped = pattern.sub("", base)
        base = stripped or base
    return base


def resolve(name: str, flags: int, weight: int, italic_angle: float) -> Resolved:
    """Catalog family and style for a PDF font (name, descriptor flags, weight, italic angle)."""
    c = catalog()
    lowered = _SUBSET.sub("", name).lower()
    bold = bool(_BOLD.search(lowered)) or weight >= 600 or bool(flags & FORCE_BOLD)
    italic = bool(_ITALIC.search(lowered)) or bool(flags & ITALIC) or italic_angle != 0
    family = c.aliases.get(normalize(name))
    if family is not None:
        return Resolved(cast(FontKey, family), bold, italic, "alias")
    kind = "mono" if flags & FIXED_PITCH else "serif" if flags & SERIF else "sans"
    return Resolved(cast(FontKey, c.generic[kind]), bold, italic, "generic")


def _style(bold: bool, italic: bool) -> str:
    return ("bold" if bold else "") + ("italic" if italic else "") or "regular"


@functools.cache
def font_name(font: str, bold: bool, italic: bool) -> str:
    """reportlab name of a catalog font, registered at first use."""
    style = _style(bold, italic)
    name = f"anonpip-{font}-{style}"
    path = files("anonymization_pipeline.resources").joinpath(
        _FOLDER, catalog().families[font][style]
    )
    pdfmetrics.registerFont(TTFont(name, str(path)))
    return name


@functools.cache
def _glyphs(name: str) -> frozenset[int]:
    font = pdfmetrics.getFont(name)
    return frozenset(font.face.charToGlyph)


def segments(text: str, font: str, bold: bool, italic: bool) -> list[tuple[str, str]]:
    """``text`` split by the font that draws it: the catalog font, else the fallback family.

    Characters neither has are replaced by ``?``.
    """
    primary = font_name(font, bold, italic)
    fallback = font_name(catalog().glyph_fallback, bold, italic)
    out: list[tuple[str, str]] = []
    for ch in text:
        if ord(ch) in _glyphs(primary):
            name = primary
        elif ord(ch) in _glyphs(fallback):
            name = fallback
        else:
            name, ch = primary, "?"
        if out and out[-1][0] == name:
            out[-1] = (name, out[-1][1] + ch)
        else:
            out.append((name, ch))
    return out


def missing(text: str, font: str, bold: bool, italic: bool) -> int:
    """Characters of ``text`` neither the font nor the fallback family can draw."""
    primary = _glyphs(font_name(font, bold, italic))
    fallback = _glyphs(font_name(catalog().glyph_fallback, bold, italic))
    return sum(1 for ch in text if ord(ch) not in primary and ord(ch) not in fallback)
