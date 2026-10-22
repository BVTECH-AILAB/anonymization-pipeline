import pytest

from src.services.anonymizer.detect import (
    allowlist,
    eponyms,
    labels,
)
from src.services.anonymizer.detect.ensemble import (
    _clean_person,
    analyze_layers,
)
from src.services.anonymizer.layers import Span, TextLayer, Token


def found(text: str) -> set[tuple[str, str]]:
    return {(s.label, s.text) for s in labels.detect(text)}


@pytest.mark.parametrize(
    ("text", "label", "value"),
    [
        ("Cognome: ROSSI", "PERSON", "ROSSI"),
        ("COGNOME / SURNAME\nBIANCHI", "PERSON", "BIANCHI"),
        (
            "Paziente: Dott.ssa Anna Maria de Luca, nata a Roma",
            "PERSON",
            "Anna Maria de Luca",
        ),
        ("nata a Roma il 01/01/1980", "LOCATION", "Roma"),
        ("Data di nascita: 01/01/1980", "DATE_OF_BIRTH", "01/01/1980"),
        (
            "N. accettazione: VS-26-004817   Data prelievo: 14/09/2026",
            "MEDICAL_RECORD",
            "VS-26-004817",
        ),
        (
            "Medico curante Dott. Paolo Iovine (cell. 335 000 0011)",
            "PERSON",
            "Paolo Iovine",
        ),
        (
            "Medico curante Dott. Paolo Iovine (cell. 335 000 0011)",
            "PHONE",
            "335 000 0011",
        ),
        ("Mediatore linguistico: Grace Nwosu - coop. Esempio", "PERSON", "Grace Nwosu"),
        ("Matricola 00412", "PROFESSIONAL_ID", "00412"),
        ("Mediatore linguistico:\nGrace Nwosu\nNote", "PERSON", "Grace Nwosu"),
        ("Residenza: Via Roma 12, Milano", "ADDRESS", "Via Roma 12, Milano"),
        ("Domicilio: San Giovanni in Fiore", "ADDRESS", "San Giovanni in Fiore"),
        (
            "Domicilio: c/o RSA Villa delle Mimose",
            "ADDRESS",
            "c/o RSA Villa delle Mimose",
        ),
    ],
)
def test_value_after_label(text: str, label: str, value: str) -> None:
    assert (label, value) in found(text)


@pytest.mark.parametrize(
    "text",
    [
        "Ricetta: amoxicillina 1 g",
        "Nome della ditta che produce",
        "Cognome: ________",
        # a form whose labels have no value: the next line is a sentence, not a name
        "Mediatore linguistico:\nAcconsento al test HIV",
        # prose after an address label is not an address
        "Assistenza a domicilio: da prenotare entro 7 giorni",
        "Domicilio: Prima fornitura del farmaco",
    ],
)
def test_label_without_a_value_of_its_shape(text: str) -> None:
    assert not found(text)


def test_spans_point_into_the_text() -> None:
    text = "Rif.\nCognome:   VERDI\nNome: Luca"
    for s in labels.detect(text):
        assert text[s.start : s.end] == s.text


@pytest.mark.parametrize(
    ("text", "word", "excluded"),
    [
        ("segno di Giordano negativo bilateralmente", "Giordano", True),
        ("Manovra di Giordano negativa", "Giordano", True),
        ("Giordano negativo al tampone", "Giordano", False),
        ("referto firmato da Marco Giordano", "Giordano", False),
        ("Glasgow 15, pupille isocoriche", "Glasgow", True),
        ("sindrome di Wolff-Parkinson-White", "Parkinson", True),
        ("in terapia con Coumadin 5 mg", "Coumadin", True),
        ("globuli rossi nella norma", "rossi", True),
        ("Dott. Rossi", "Rossi", False),
    ],
)
def test_eponym_decision_depends_on_each_occurrence(
    text: str, word: str, excluded: bool
) -> None:
    start = text.index(word)

    assert eponyms.is_excluded(text, start, start + len(word)) is excluded


def test_person_spans_lose_titles_and_role_words() -> None:
    text = "Dott.ssa\nAnna Rossi e la paziente"
    titled = _clean_person(text, Span(0, 19, "PERSON", 0.9, "ner", text[:19]))
    role = _clean_person(text, Span(22, 33, "PERSON", 0.9, "ner", "la paziente"))

    assert titled is not None and titled.text == "Anna Rossi"
    assert role is None


def test_single_word_after_a_role_label_is_not_a_name() -> None:
    assert not found("Il Medico Ostetrico")
    assert ("PERSON", "ROSSI") in found("Medico ROSSI")
    assert ("PERSON", "Rossi") in found("Medico: Rossi")


def test_identifiers_and_addresses_are_propagated_to_every_occurrence() -> None:
    first = TextLayer.build(
        0,
        "native",
        [
            Token("Nosologico:", (0, 0, 60, 10), [(0, 0, 6, 10)] * 11, 0, "native"),
            Token(
                "AB-2024-001234", (70, 0, 150, 10), [(70, 0, 76, 10)] * 14, 0, "native"
            ),
            Token("residente", (0, 20, 50, 30), [(0, 20, 5, 30)] * 9, 1, "native"),
            Token("in", (55, 20, 65, 30), [(55, 20, 60, 30)] * 2, 1, "native"),
            Token("Via", (70, 20, 85, 30), [(70, 20, 75, 30)] * 3, 1, "native"),
            Token("Roma", (90, 20, 110, 30), [(90, 20, 95, 30)] * 4, 1, "native"),
            Token("14", (115, 20, 125, 30), [(115, 20, 120, 30)] * 2, 1, "native"),
        ],
    )
    second = TextLayer.build(
        1,
        "native",
        [
            Token("rif.", (0, 0, 20, 10), [(0, 0, 5, 10)] * 4, 0, "native"),
            Token(
                "ab-2024-001234", (25, 0, 100, 10), [(25, 0, 30, 10)] * 14, 0, "native"
            ),
            Token("Via", (0, 20, 15, 30), [(0, 20, 5, 30)] * 3, 1, "native"),
            Token("Roma", (20, 20, 40, 30), [(20, 20, 25, 30)] * 4, 1, "native"),
            Token("14", (45, 20, 55, 30), [(45, 20, 50, 30)] * 2, 1, "native"),
        ],
    )

    findings = analyze_layers([first, second])

    on_second = {(f.label, f.text) for f in findings if f.layer is second}
    assert ("MEDICAL_RECORD", "ab-2024-001234") in on_second
    assert ("ADDRESS", "Via Roma 14") in on_second


@pytest.mark.parametrize(
    "text", ["test Rossi positivo", "metodo Bianchi applicato", "score Verdani 3"]
)
def test_a_surname_after_a_clinical_word_is_still_a_person(text: str) -> None:
    name = text.split()[1]
    start = text.index(name)

    assert not allowlist.is_eponym_use(text, start, start + len(name))
