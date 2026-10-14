import copy
from importlib.resources import files
from typing import Any, get_args

import pytest

from anonymization_pipeline.layout import fonts
from anonymization_pipeline.layout.model import FontKey
from anonymization_pipeline.pipeline_config.resources import read_yaml


@pytest.mark.parametrize(
    ("name", "family"),
    [
        ("ABCDEF+TimesNewRomanPS-BoldItalicMT", "timesnewroman"),
        ("TimesNewRomanPSMT", "timesnewroman"),
        ("Arial-BoldMT", "arial"),
        ("Arial,Bold", "arial"),
        ("ArialNarrow-Bold", "arial"),
        ("Times-Roman", "times"),
        ("BCDEEE+Calibri", "calibri"),
        ("HelveticaNeue-Bold", "helveticaneue"),
        ("", ""),
    ],
)
def test_normalize(name: str, family: str) -> None:
    assert fonts.normalize(name) == family


@pytest.mark.parametrize(
    ("name", "font", "bold", "italic"),
    [
        ("ABCDEF+TimesNewRomanPS-BoldItalicMT", "liberation-serif", True, True),
        ("Helvetica", "liberation-sans", False, False),
        ("Courier-Bold", "liberation-mono", True, False),
        ("Calibri-Italic", "carlito", False, True),
        ("Cambria", "caladea", False, False),
        ("Verdana", "dejavu-sans", False, False),
    ],
)
def test_known_fonts_resolve_to_their_alias(
    name: str, font: str, bold: bool, italic: bool
) -> None:
    resolved = fonts.resolve(name, 0, 400, 0)
    assert (resolved.font, resolved.bold, resolved.italic, resolved.how) == (
        font,
        bold,
        italic,
        "alias",
    )


def test_unknown_fonts_resolve_by_their_flags() -> None:
    assert fonts.resolve("Garamond", fonts.SERIF, 400, 0).font == "liberation-serif"
    assert (
        fonts.resolve("Consolas", fonts.FIXED_PITCH, 400, 0).font == "liberation-mono"
    )
    unknown = fonts.resolve("XYZ+Whatever", 0, 700, -12)
    assert unknown == fonts.Resolved("liberation-sans", True, True, "generic")
    assert fonts.resolve("Whatever", fonts.FORCE_BOLD | fonts.ITALIC, 0, 0).bold


def test_every_family_has_four_styles_and_a_license() -> None:
    catalog = fonts.catalog()
    assert set(catalog.families) == set(get_args(FontKey))
    folder = files("anonymization_pipeline.resources").joinpath("fonts")
    licenses = [p.name for p in folder.iterdir() if p.name.startswith("LICENSE-")]
    assert {
        "LICENSE-Liberation.txt",
        "LICENSE-Carlito.txt",
        "LICENSE-Caladea.txt",
    } <= set(licenses)
    for family in catalog.families:
        for bold in (False, True):
            for italic in (False, True):
                assert fonts.font_name(family, bold, italic).startswith("anonpip-")


def test_missing_glyphs_go_to_the_fallback_family() -> None:
    parts = fonts.segments("Ciao ❤ █", "caladea", False, False)

    assert parts == [
        ("anonpip-caladea-regular", "Ciao "),
        ("anonpip-dejavu-sans-regular", "❤"),
        ("anonpip-caladea-regular", " "),
        ("anonpip-dejavu-sans-regular", "█"),
    ]
    assert fonts.missing("a中", "liberation-sans", False, False) == 1
    assert fonts.segments("中", "liberation-sans", False, False) == [
        ("anonpip-liberation-sans-regular", "?")
    ]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.pop("generic"), "missing keys generic"),
        (lambda d: d.update(extra=1), "unknown keys extra"),
        (lambda d: d["families"].pop("carlito"), "missing keys carlito"),
        (
            lambda d: d["families"]["carlito"].update(regular="Nope.ttf"),
            "missing file Nope.ttf",
        ),
        (lambda d: d["aliases"]["carlito"].append("arial"), "listed twice"),
        (lambda d: d["aliases"]["carlito"].append("Calibri-Bold"), "not normalized"),
        (lambda d: d["aliases"].update(comic=["comic"]), "unknown family"),
        (lambda d: d.update(glyph_fallback="comic"), "catalog families"),
    ],
)
def test_an_invalid_catalog_is_rejected(
    monkeypatch: pytest.MonkeyPatch, change: Any, message: str
) -> None:
    data = copy.deepcopy(read_yaml(fonts.FILE))
    change(data)
    monkeypatch.setattr(fonts, "read_yaml", lambda name: data)
    fonts.catalog.cache_clear()
    try:
        with pytest.raises(ValueError, match=message):
            fonts.catalog()
    finally:
        fonts.catalog.cache_clear()
