import copy
from typing import Any

import pytest

from anonymization_pipeline.detectors import eponyms
from anonymization_pipeline.detectors.eponyms import FILE, is_excluded, lists


def _excluded(text: str, value: str, category: str = "COGNOME") -> bool:
    """``is_excluded`` on the first occurrence of ``value`` in ``text``."""
    start = text.index(value)
    return is_excluded(text, start, start + len(value), category)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Paziente affetto da morbo di Parkinson.", "Parkinson"),
        ("Anamnesi: M. di Crohn in remissione", "Crohn"),
        ("Manovra Valsalva eseguita", "Valsalva"),
        ("Sindrome di **Down**", "Down"),
        ("morbo d'Alzheimer", "Alzheimer"),
        ("ECG: pattern Wolff-Parkinson-White", "Parkinson"),
        ("ECG: pattern Wolff - Parkinson - White", "Wolff"),
        ("Guillain-Barré", "Guillain-Barré"),
        ("Giordano negativo bilateralmente", "Giordano"),
        ("Murphy pos.", "Murphy"),
        ("Blumberg -, Murphy -", "Blumberg"),
        ("| Lasègue | positivo |", "Lasègue"),
        ("Glasgow 15", "Glasgow"),
        ("GCS: Glasgow 15.", "Glasgow"),
        ("Apgar 9/10", "Apgar"),
        ("eseguito Holter delle 24 ore", "Holter"),
        ("in terapia con Coumadin", "Coumadin"),
        ("globuli rossi nella norma", "rossi"),
    ],
)
def test_not_a_person_in_a_medical_context(text: str, value: str) -> None:
    assert _excluded(text, value)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Paziente: Parkinson Mario", "Parkinson"),
        ("Sig. Giordano, nato a Roma", "Giordano"),
        ("Dott.ssa Murphy", "Murphy"),
        ("Giordano 45 anni", "Giordano"),
        ("Paziente: M. Parkinson", "Parkinson"),
        ("Giordano - Via Roma 12", "Giordano"),
        ("Diagnosi: Parkinson", "Parkinson"),
        ("Glasgow 45 anni", "Glasgow"),
        ("Glasgow 12/03/1980", "Glasgow"),
        ("Glasgow Ivan", "Glasgow"),
        ("Rossi Mario, globuli rossi", "Rossi"),
        ("Paziente: Mario Rossi", "Rossi"),
    ],
)
def test_a_person_everywhere_else(text: str, value: str) -> None:
    assert not _excluded(text, value)


def test_categories() -> None:
    text = "manovra di Valsalva, Coumadin, morbo di Parkinson"
    assert _excluded("Sig. Valsalva", "Valsalva", "FARMACO")  # never a drug
    assert _excluded(text, "Valsalva", "MEDICO")
    assert not _excluded(text, "Coumadin", "FARMACO")  # a real drug stays a drug
    assert not _excluded(
        text, "Parkinson", "DIAGNOSI"
    )  # a diagnosis is never in clear text
    assert not _excluded(text, "Valsalva", "INDIRIZZO")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(extra=[]), "unknown keys extra"),
        (lambda d: d.pop("nouns"), "missing keys nouns"),
        (lambda d: d["drugs"].append("Parkinson"), "'parkinson' listed twice"),
        (lambda d: d["eponyms"].append("Addison"), "'addison' listed twice"),
        (lambda d: d.update(scales="Apgar"), "scales: expected a list"),
        (
            lambda d: d["context"].update(after="(positivo"),
            "context.after: invalid regex",
        ),
        (lambda d: d["context"].update(window=0), "context.window"),
        (lambda d: d["context"].pop("score"), "missing keys score"),
    ],
)
def test_file_is_validated(
    monkeypatch: pytest.MonkeyPatch, change: Any, message: str
) -> None:
    data = copy.deepcopy(eponyms.read_yaml(FILE))
    change(data)
    monkeypatch.setattr(eponyms, "read_yaml", lambda name: data)
    lists.cache_clear()
    try:
        with pytest.raises(ValueError, match=message):
            lists()
    finally:
        lists.cache_clear()
