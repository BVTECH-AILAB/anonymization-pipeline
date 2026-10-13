"""Markdown report of a run."""

from anonval.report import ProfileRun, pct, render_report
from anonval.scoring import (
    DocScore,
    Hit,
    ValueOutcome,
    error_score,
    summarize,
    verdict,
)
from anonval.settings import Thresholds


def _doc(leak: bool) -> DocScore:
    doc = DocScore(
        "referto_esterno_media_02",
        "referto_esterno",
        "media",
        "completed",
        duration_s=40.0,
    )
    doc.values = [
        ValueOutcome(
            "Anna Riva",
            "PERSONA",
            "anagrafica",
            ["stamp"],
            True,
            True,
            True,
            not leak,
            True,
            True,
        ),
        ValueOutcome(
            "Via Po | 3",
            "INDIRIZZO",
            "residenza",
            ["footer"],
            True,
            True,
            True,
            True,
            False,
            False,
        ),
        ValueOutcome(
            "Ugo Neri", "FAMILIARE", "persone_correlate", ["paragraph"], True, False
        ),
    ]
    doc.values[0].sources = ["labels"]
    doc.values[0].predicted = ["PERSONA"]
    doc.values[0].leak_pages = [1]
    doc.values[2].pages = [3]
    doc.tp = 2
    doc.false_positives = [Hit("Gleason", "PERSONA", ["detection"], 2)]
    doc.negatives_violated = [Hit("Gleason", "PERSONA", ["detection"], 2)]
    return doc


def _run(name: str, leak: bool) -> ProfileRun:
    docs = [
        _doc(leak),
        error_score(
            {"id": "x_facile_01", "type": "referto_esterno", "difficulty": "facile"},
            "error",
            "HTTP 500",
        ),
    ]
    summary = summarize(docs)
    return ProfileRun(
        name,
        "descrizione",
        {"mode": "tag"},
        summary,
        verdict(summary["overall"], Thresholds()),
        docs,
    )


def test_pct_uses_italian_format() -> None:
    assert pct(0.9523) == "95,2%"
    assert pct(None) == "n.d."


def test_report_has_every_section() -> None:
    text = render_report(
        {"Servizio": "0.1.0"},
        [_run("completo", True), _run("regole", False)],
        Thresholds(),
    )
    for heading in (
        "## Sintesi",
        "## Confronto tra profili",
        "## Profilo `completo`",
        "### Per categoria",
        "### Tipo x difficoltà",
        "### Per posizione nel layout",
        "### Valori rimasti in chiaro",
        "### Documenti respinti o in errore",
    ):
        assert heading in text
    assert (
        '  - "Anna Riva" (PERSONA): in chiaro a pag. 1; nel layout: timbro; '
        "censurato altrove come PERSONA da etichette"
    ) in text
    assert '  - "Ugo Neri" (FAMILIARE): nel PDF a pag. 3; nel layout: paragrafo' in text
    assert '  - "Gleason" PERSONA: pag. 2, trovato da VLM' in text
    assert '  - "Gleason" come PERSONA: pag. 2, trovato da VLM' in text
    assert "| `completo` | FAIL |" in text
    assert "x_facile_01`: ERRORE - HTTP 500" in text
