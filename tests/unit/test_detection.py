import pytest

from anonmyzation_pipeline.services.anonymization.detect import allowlist
from anonmyzation_pipeline.services.anonymization.detect.ensemble import analyze_layers
from anonmyzation_pipeline.services.anonymization.layers import TextLayer, Token
from tests.conftest import FAKE_CF
from tests.documents import FakeNER


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


def test_identifiers_are_propagated_without_context() -> None:
    doc = layer("Nosologico 2026123456\nfooter 2026123456")

    findings = analyze_layers([doc])

    assert len([f for f in findings if f.label == "MEDICAL_RECORD"]) == 2


def test_overlapping_spans_merge_into_the_most_specific_label() -> None:
    doc = layer(f"codice {FAKE_CF}")

    class GreedyNER(FakeNER):
        def detect(self, text: str):
            from anonmyzation_pipeline.services.anonymization.layers import Span

            i = text.index(FAKE_CF)
            return [Span(i, i + len(FAKE_CF), "PERSON", 0.9, "fake-ner", FAKE_CF)]

    findings = analyze_layers([doc], GreedyNER())

    assert [(f.label, f.text) for f in findings] == [("FISCAL_CODE", FAKE_CF)]


def test_ner_threshold_filters_weak_entities() -> None:
    doc = layer("visto da Gino Bartali oggi")

    class WeakNER(FakeNER):
        def detect(self, text: str):
            from anonmyzation_pipeline.services.anonymization.layers import Span

            i = text.index("Gino")
            return [Span(i, i + 12, "PERSON", 0.3, "fake-ner", "Gino Bartali")]

    assert texts(analyze_layers([doc], WeakNER()), "PERSON") == set()
    assert texts(analyze_layers([doc], WeakNER(), person_threshold=0.2), "PERSON") == {
        "Gino Bartali"
    }


def test_url_containing_a_tax_code_is_labelled_url() -> None:
    doc = layer(f"online: https://referti.example.it/r?id=2026123456&cf={FAKE_CF}")

    findings = analyze_layers([doc])

    assert [f.label for f in findings] == ["URL"]
