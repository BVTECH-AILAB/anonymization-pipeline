from anonymization_pipeline.domain import Entity
from anonymization_pipeline.redaction import group_entities, redact
from tests.pipeline import pipeline_config


def E(text: str, category: str, page: int = 0) -> Entity:
    return Entity(text, category, page)


def test_consistent_tags_across_pages_in_order_of_appearance() -> None:
    cfg = pipeline_config()
    texts = [
        "Medico: Anna Bianchi. Paziente: Mario Rossi",
        "MARIO ROSSI torna. Firma Anna Bianchi",
    ]
    groups = group_entities(
        [
            E("Mario Rossi", "PERSONA"),
            E("Anna Bianchi", "MEDICO"),
            E("MARIO  ROSSI", "PERSONA", 1),
        ]
    )

    pages, occurrences = redact(texts, groups, cfg)

    assert pages == [
        "Medico: [MEDICO_1]. Paziente: [PERSONA_1]",
        "[PERSONA_1] torna. Firma [MEDICO_1]",
    ]
    for page, found in zip(pages, occurrences, strict=True):
        for o in found:
            assert page[o.start : o.end] == o.replacement


def test_longest_span_wins_and_propagation() -> None:
    cfg = pipeline_config()
    groups = group_entities([E("Rossi", "COGNOME"), E("Mario Rossi", "PERSONA")])

    pages, _ = redact(["Mario Rossi; la sig.ra ROSSI"], groups, cfg)

    assert pages == ["[PERSONA_1]; la sig.ra [COGNOME_1]"]


def test_redaction_mode() -> None:
    cfg = pipeline_config(default_mode="redaction")
    groups = group_entities([E("RSSMRA85M01H501Q", "CODICE_FISCALE")])

    assert redact(["CF: RSSMRA85M01H501Q"], groups, cfg)[0] == ["CF: █████"]


def test_only_the_same_form_shares_a_tag() -> None:
    # spacing and case variants go together; different spellings of a name, different dates and
    # values of different families do not
    groups = group_entities(
        [
            E("Mario Rossi", "PERSONA"),
            E("MARIO  ROSSI", "PERSONA"),
            E("Mario Rossi", "PERSONA"),
            E("Rossi Mario", "PERSONA"),
            E("01/03/1980", "DATA_NASCITA"),
            E("12/09/2026", "DATA_NASCITA"),
            E("Roma", "LUOGO_NASCITA"),
            E("Roma", "INDIRIZZO"),
        ]
    )

    assert [sorted(e.text for e in g.values) for g in groups] == [
        ["MARIO  ROSSI", "Mario Rossi"],
        ["Rossi Mario"],
        ["01/03/1980"],
        ["12/09/2026"],
        ["Roma"],
        ["Roma"],
    ]


def test_group_category_is_the_most_informative() -> None:
    groups = group_entities(
        [E("Rossi", "COGNOME"), E("rossi", "PERSONA"), E("ROSSI", "MEDICO")]
    )

    assert [g.category for g in groups] == ["MEDICO"]


def test_common_words_in_lowercase_are_not_redacted() -> None:
    cfg = pipeline_config()
    groups = group_entities([E("Rossi", "COGNOME")])

    pages, _ = redact(["Paziente Rossi, globuli rossi"], groups, cfg)

    assert pages == ["Paziente [COGNOME_1], globuli rossi"]


def test_initials_are_not_redacted_inside_dotted_abbreviations() -> None:
    cfg = pipeline_config()
    groups = group_entities([E("M.", "PERSONA")])

    pages, _ = redact(["Rossi M. - U.M. mg/dL"], groups, cfg)

    assert pages == ["Rossi [PERSONA_1] - U.M. mg/dL"]
