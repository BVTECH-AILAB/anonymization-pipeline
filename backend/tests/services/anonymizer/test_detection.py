import pytest

from src.services.anonymizer.detect import allowlist
from src.services.anonymizer.detect.ensemble import analyze_layers
from src.services.anonymizer.layers import Span, TextLayer, Token
from tests.documents import FAKE_CF, FakeNER


class FakeAddressNER:
    """NER stand-in that proposes one value with one label wherever it appears."""

    def __init__(self, value: str, label: str = "ADDRESS") -> None:
        self.value, self.label = value, label

    @property
    def ready(self) -> bool:
        return True

    def load(self) -> None:
        return None

    def detect(self, text: str) -> list[Span]:
        i = text.find(self.value)
        if i < 0:
            return []
        return [Span(i, i + len(self.value), self.label, 0.9, "ailab-ner", self.value)]


def layer(text: str, page: int = 0) -> TextLayer:
    """A layer whose tokens are the whitespace-separated words of ``text``, one line per text line."""
    tokens, x = [], 0.0
    for line_no, line in enumerate(text.split("\n")):
        x = 0.0
        for word in line.split(" "):
            if not word:
                continue
            chars = [
                (x + i * 5, line_no * 12.0, x + (i + 1) * 5, line_no * 12.0 + 10.0)
                for i in range(len(word))
            ]
            tokens.append(
                Token(
                    word,
                    (x, line_no * 12.0, x + len(word) * 5, line_no * 12.0 + 10.0),
                    chars,
                    line_no,
                    "native",
                )
            )
            x += len(word) * 5 + 5
    return TextLayer.build(page, "native", tokens)


def texts(findings, label: str) -> set[str]:
    return {f.text for f in findings if f.label == label}


@pytest.mark.parametrize(
    ("text", "start", "end", "eponym"),
    [
        ("morbo di Crohn", 9, 14, True),
        ("riflesso di Babinski assente", 12, 20, True),
        ("organo del Corti", 11, 16, True),
        ("Glasgow Coma Scale 15", 0, 7, True),
        ("Sig. Corti si presenta", 5, 10, False),
        ("la paziente Concetta Corti", 12, 26, False),
    ],
)
def test_eponym_use_depends_on_context(
    text: str, start: int, end: int, eponym: bool
) -> None:
    assert allowlist.is_eponym_use(text, start, end) is eponym


def test_saints_drugs_and_clinical_words_are_not_pii() -> None:
    assert allowlist.is_not_pii("Ospedale San Giovanni", 12, 21, "PERSON")
    assert allowlist.is_not_pii("terapia con metformina", 12, 22, "PERSON")
    assert allowlist.is_not_pii("Gentile Collega", 0, 15, "PERSON")
    assert not allowlist.is_not_pii("Dott. Mario Rossi", 6, 17, "PERSON")


def test_propagation_finds_every_form_of_a_name() -> None:
    page1 = layer(f"Paziente: Mario Rossi\nC.F. {FAKE_CF}")
    page2 = layer("Il Sig. Rossi riferisce\nfirma ROSSI M.\nROSSIMARIO", page=1)

    findings = analyze_layers([page1, page2], FakeNER(("Mario Rossi",)))

    persons = texts(findings, "PERSON")
    assert "Mario Rossi" in persons
    assert any("Rossi" in p for p in persons if p != "Mario Rossi")
    assert "ROSSI M." in persons
    assert "ROSSIMARIO" in persons


def test_patient_with_eponym_surname_keeps_the_eponym() -> None:
    doc = layer("Paziente: Concetta Corti\nipoacusia da danno dell'organo del Corti")

    findings = analyze_layers([doc], FakeNER(("Concetta Corti",)))

    corti = [f for f in findings if f.label == "PERSON" and "Corti" in f.text]
    assert len(corti) == 1
    assert corti[0].text == "Concetta Corti"


def test_common_word_first_name_is_not_propagated() -> None:
    doc = layer("Dott.ssa Bianca Papafava\nlesioni della sostanza bianca")

    findings = analyze_layers([doc], FakeNER(("Bianca Papafava",)))

    assert all(
        "sostanza" not in f.layer.text[f.start - 9 : f.start]
        for f in findings
        if f.text == "bianca"
    )
    assert "bianca" not in texts(findings, "PERSON")


def test_acronyms_and_field_labels_inside_a_name_are_not_spread() -> None:
    # a wrong span that swallowed "HIV" or a label must not mark every occurrence
    doc = layer(
        "Paziente: Mario Rossi HIV Recapito\ntest HIV negativo\nRecapito: ufficio"
    )

    findings = analyze_layers([doc], FakeNER(("Mario Rossi HIV Recapito",)))

    spread = [f.text for f in findings if f.label == "PERSON" and f.start > 40]
    assert "HIV" not in spread and "Recapito" not in spread


def test_short_and_ambiguous_words_are_spread_only_with_the_full_name() -> None:
    doc = layer(
        "Paziente: UGO NAPOLI\nfirma: Napoli Ugo\nnato a Napoli\nvisto Ugo\nRossi Govoni"
    )

    findings = analyze_layers([doc], FakeNER(("UGO NAPOLI", "Rossi Govoni")))

    persons = texts(findings, "PERSON")
    assert "Napoli Ugo" in persons  # the full name, in any order
    assert "Napoli" not in persons and "Ugo" not in persons  # never alone
    assert "Rossi Govoni" in persons


def test_ocr_fragments_and_labels_are_not_names_or_places() -> None:
    for text, label in [
        ("paz NGUY Q 3", "PERSON"),
        ("emb T $ Esa Neuro", "PERSON"),
        ("Telefono", "LOCATION"),
    ]:
        assert allowlist.is_not_pii(text, 0, len(text), label)
    for text in ("ROSSI^MARIO", "D'Amico Rosa", "Rossi, Mario", "I. Cerul1o"):
        assert not allowlist.is_not_pii(text, 0, len(text), "PERSON")


@pytest.mark.parametrize(
    ("value", "kept"),
    [
        ("Via Gaspare Nadi 55", True),
        ("Udine, Via Santa Caterina, 19", True),
        ("San Giovanni in Fiore", True),
        ("Vico Lungo Gelso 9", True),
        ("3150 Calle Del Sol", True),
        ("35028", True),
        ("ACC dx 68 16", False),
        ("AV sX 38 10", False),
    ],
)
def test_an_address_from_ner_needs_the_shape_of_one(value: str, kept: bool) -> None:
    doc = layer(f"Nota: {value}")

    findings = analyze_layers([doc], FakeAddressNER(value))

    assert (value in texts(findings, "ADDRESS")) is kept


def test_a_facility_is_not_a_place() -> None:
    doc = layer("Sistema Socio Sanitario Regionale ASST Parma")

    findings = analyze_layers(
        [doc], FakeAddressNER("Sistema Socio Sanitario Regionale", "LOCATION")
    )

    assert not texts(findings, "LOCATION")


@pytest.mark.parametrize(
    ("text", "name", "spread"),
    [
        # hidden OCR layer of a scanner: words glued, letters misread as digits
        (
            "Paziente: Linda Marsh\nprevpt:LindaMarshRX-550-2877",
            "Linda Marsh",
            "LindaMarsh",
        ),
        (
            "Sig. Adebayo Okafor\nrichiamare Sig. Adebay0 ce11",
            "Adebayo Okafor",
            "Adebay0",
        ),
        # a surname glued to an initial in a code in capitals
        (
            "Dott. Thomas J. Okafor\nLIC/RPT/TOKAFOR/print",
            "Thomas J. Okafor",
            "TOKAFOR",
        ),
    ],
)
def test_names_are_found_glued_and_misread_by_ocr(
    text: str, name: str, spread: str
) -> None:
    findings = analyze_layers([layer(text)], FakeNER((name,)))

    assert spread in texts(findings, "PERSON")


def test_a_longer_word_is_not_taken_for_a_glued_name() -> None:
    findings = analyze_layers(
        [layer("Dott. Mario Rossi\nla TROSSA cucina\nMarshall")],
        FakeNER(("Mario Rossi",)),
    )

    assert texts(findings, "PERSON") == {"Mario Rossi"}


def test_codes_of_the_same_scheme_and_misread_numbers_are_found() -> None:
    doc = layer(
        "Nosologico RX-550-2913\nprev pt: RX-550-2877\nTel. 389 555 7731\nrichiamare 389 555 773l"
    )

    found = {(f.label, f.text) for f in analyze_layers([doc], FakeNER(()))}

    assert ("MEDICAL_RECORD", "RX-550-2877") in found
    assert ("PHONE", "389 555 773l") in found


def test_identifiers_are_propagated_without_context() -> None:
    doc = layer("Nosologico 2026123456\nfooter 2026123456")

    findings = analyze_layers([doc])

    assert len([f for f in findings if f.label == "MEDICAL_RECORD"]) == 2


def test_overlapping_spans_merge_into_the_most_specific_label() -> None:
    doc = layer(f"codice {FAKE_CF}")

    class GreedyNER(FakeNER):
        def detect(self, text: str):
            i = text.index(FAKE_CF)
            return [Span(i, i + len(FAKE_CF), "PERSON", 0.9, "fake-ner", FAKE_CF)]

    findings = analyze_layers([doc], GreedyNER())

    assert [(f.label, f.text) for f in findings] == [("FISCAL_CODE", FAKE_CF)]


def test_ner_threshold_filters_weak_entities() -> None:
    doc = layer("visto da Gino Verdani oggi")

    class WeakNER(FakeNER):
        def detect(self, text: str):
            i = text.index("Gino")
            return [Span(i, i + 12, "PERSON", 0.3, "fake-ner", "Gino Verdani")]

    assert texts(analyze_layers([doc], WeakNER()), "PERSON") == set()
    assert texts(analyze_layers([doc], WeakNER(), person_threshold=0.2), "PERSON") == {
        "Gino Verdani"
    }


def test_url_containing_a_tax_code_is_labelled_url() -> None:
    doc = layer(f"online: https://referti.example.it/r?id=2026123456&cf={FAKE_CF}")

    findings = analyze_layers([doc])

    assert [f.label for f in findings] == ["URL"]
