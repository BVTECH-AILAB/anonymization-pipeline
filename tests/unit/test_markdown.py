from typing import Any

from anonymization_pipeline.pipeline_config import PipelineConfig, load_pipeline_config
from anonymization_pipeline.render.markdown import (
    Leftover,
    group_values,
    leftovers,
    redact_texts,
    render,
)
from anonymization_pipeline.text import replacement_spans, tag_category
from tests.conftest import DEFAULTS_FILE, FAKE_CF, make_settings


def _cfg(**redaction: Any) -> PipelineConfig:
    cfg = load_pipeline_config(make_settings(pipeline_config_file=DEFAULTS_FILE))
    return cfg.model_copy(
        update={"redaction": cfg.redaction.model_copy(update=redaction)}
    )


def test_consistent_tags_across_pages_in_order_of_appearance() -> None:
    texts = [
        "Medico: Anna Bianchi. Paziente: Mario Rossi",
        "MARIO ROSSI torna. Firma Anna Bianchi",
    ]
    groups = group_values(
        {
            ("Mario Rossi", "PERSONA"),
            ("Anna Bianchi", "MEDICO"),
            ("MARIO  ROSSI", "PERSONA"),
        }
    )

    pages, occurrences = redact_texts(texts, groups, _cfg())

    assert pages == [
        "Medico: [MEDICO_1]. Paziente: [PERSONA_1]",
        "[PERSONA_1] torna. Firma [MEDICO_1]",
    ]
    for page, found in zip(pages, occurrences, strict=True):
        for o in found:
            assert page[o.start : o.end] == o.replacement


def test_longest_span_wins_and_propagation() -> None:
    groups = group_values({("Rossi", "COGNOME"), ("Mario Rossi", "PERSONA")})

    pages, _ = redact_texts(["Mario Rossi; la sig.ra ROSSI"], groups, _cfg())

    assert pages == ["[PERSONA_1]; la sig.ra [COGNOME_1]"]


def test_redaction_mode() -> None:
    groups = group_values({(FAKE_CF, "CODICE_FISCALE")})

    pages, _ = redact_texts([f"CF: {FAKE_CF}"], groups, _cfg(mode="redaction"))

    assert pages == ["CF: █████"]


def test_only_the_same_form_shares_a_tag() -> None:
    # spacing and case variants go together; different spellings of a name, different dates and
    # values of different families do not
    groups = group_values(
        {
            ("Mario Rossi", "PERSONA"),
            ("MARIO  ROSSI", "PERSONA"),
            ("Rossi Mario", "PERSONA"),
            ("01/03/1980", "DATA_NASCITA"),
            ("12/09/2026", "DATA_NASCITA"),
            ("Roma", "LUOGO_NASCITA"),
            ("Roma", "INDIRIZZO"),
        }
    )

    assert sorted(sorted(t for t, _ in g.values) for g in groups) == sorted(
        [
            ["MARIO  ROSSI", "Mario Rossi"],
            ["Rossi Mario"],
            ["01/03/1980"],
            ["12/09/2026"],
            ["Roma"],
            ["Roma"],
        ]
    )


def test_group_category_is_the_most_informative() -> None:
    groups = group_values(
        {("Rossi", "COGNOME"), ("rossi", "PERSONA"), ("ROSSI", "MEDICO")}
    )

    assert [g.category for g in groups] == ["MEDICO"]


def test_common_words_in_lowercase_are_not_redacted() -> None:
    groups = group_values({("Rossi", "COGNOME")})

    pages, _ = redact_texts(["Paziente Rossi, globuli rossi"], groups, _cfg())

    assert pages == ["Paziente [COGNOME_1], globuli rossi"]


def test_initials_are_not_redacted_inside_dotted_abbreviations() -> None:
    groups = group_values({("M.", "PERSONA")})

    pages, _ = redact_texts(["Rossi M. - U.M. mg/dL"], groups, _cfg())

    assert pages == ["Rossi [PERSONA_1] - U.M. mg/dL"]


def test_a_value_across_table_cells_gets_the_tag_in_every_cell() -> None:
    groups = group_values(
        {("derivazione ventricolare esterna frontale destra", "INTERVENTO")}
    )
    row = "| Derivazione ventricolare esterna | frontale destra (Kocher) | 03/03 |"

    pages, occurrences = redact_texts([row], groups, _cfg())

    assert pages == ["| [INTERVENTO_1] | [INTERVENTO_1] (Kocher) | 03/03 |"]
    whole = "Derivazione ventricolare esterna frontale destra"
    assert [(o.original, o.value) for o in occurrences[0]] == [
        ("Derivazione ventricolare esterna", whole),
        ("frontale destra", whole),
    ]
    for o in occurrences[0]:
        assert pages[0][o.start : o.end] == o.replacement


def test_pages_are_joined_and_offsets_point_into_the_document() -> None:
    values = {("Mario Rossi", "PERSONA"), (FAKE_CF, "CODICE_FISCALE")}

    rendered, left = render(["Paziente Mario Rossi", f"CF {FAKE_CF}"], values, _cfg())

    assert rendered.markdown == (
        "Paziente [PERSONA_1]\n\n<!-- pagina 2 -->\n\nCF [CODICE_FISCALE_1]"
    )
    assert left == []
    for o in rendered.occurrences:
        assert rendered.markdown[o.start : o.end] == o.replacement


def test_a_value_left_in_clear_text_is_reported_without_the_value() -> None:
    cfg = _cfg()
    values = {("Mario Rossi", "PERSONA"), ("Parkinson", "COGNOME")}
    texts = ["Paziente [PERSONA_1] e Mario Rossi", "morbo di Parkinson"]

    left = leftovers(texts, values, cfg)

    # the tag is not a leftover, the clear copy is; the eponym is left on purpose
    assert left == [Leftover("PERSONA", 1)]


def test_tags_are_recognised() -> None:
    redaction = _cfg().redaction

    assert tag_category("[CODICE_FISCALE_2]", redaction) == "CODICE_FISCALE"
    assert tag_category("█████", redaction) is None
    text = "[PERSONA_1] e Rossi e █████"
    assert [text[s:e] for s, e in replacement_spans(text, redaction)] == [
        "[PERSONA_1]",
        "█████",
    ]
