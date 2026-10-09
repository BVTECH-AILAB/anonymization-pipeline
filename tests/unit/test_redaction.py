import asyncio
import json
from typing import Any

from anonymization_pipeline.domain import Entity, Group
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.redaction import group_entities, redact
from anonymization_pipeline.vlm import VLMError
from tests.pipeline import Answer, FakeVLM, pipeline_config


def E(text: str, category: str, page: int = 0) -> Entity:
    return Entity(text, category, page)


def _groups(
    cfg: PipelineConfig, entities: list[Entity], answer: Answer | None = None
) -> tuple[list[Group], dict[str, Any]]:
    client = FakeVLM(
        cfg, lambda *args: answer if answer is not None else '{"groups": []}'
    )
    return asyncio.run(group_entities(entities, client, cfg))


def test_consistent_tags_across_pages_in_order_of_appearance() -> None:
    cfg = pipeline_config()
    texts = [
        "Medico: Anna Bianchi. Paziente: Mario Rossi",
        "Rossi Mario torna. Firma Anna Bianchi",
    ]
    groups, _ = _groups(
        cfg,
        [
            E("Mario Rossi", "PERSONA"),
            E("Anna Bianchi", "MEDICO"),
            E("Rossi Mario", "PERSONA", 1),
        ],
        '{"groups": [[1, 3], [2]]}',
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
    groups, _ = _groups(cfg, [E("Rossi", "COGNOME"), E("Mario Rossi", "PERSONA")])

    pages, _ = redact(["Mario Rossi; la sig.ra ROSSI"], groups, cfg)

    assert pages == ["[PERSONA_1]; la sig.ra [COGNOME_1]"]


def test_coreference_answer_is_bounded_by_the_number_of_values() -> None:
    cfg = pipeline_config()
    schemas: list[dict[str, Any]] = []

    def answer(phase: str, system: str, user: str, images: list, schema: Any) -> str:
        schemas.append(schema)
        return '{"groups": [[1, 2], [3]]}'

    client = FakeVLM(cfg, answer)
    values = [
        E("Mario Rossi", "PERSONA"),
        E("ROSSI MARIO", "PERSONA"),
        E("Anna Bianchi", "MEDICO"),
    ]

    asyncio.run(group_entities(values, client, cfg))

    groups = schemas[0]["properties"]["groups"]
    assert groups["maxItems"] == 3 and groups["items"]["maxItems"] == 3
    assert groups["items"]["items"] == {"type": "integer", "minimum": 1, "maximum": 3}
    # bounded, but with room for a model that reasons before answering
    assert client.max_tokens == [1024]


def test_redaction_mode() -> None:
    cfg = pipeline_config(default_mode="redaction")
    groups, _ = _groups(cfg, [E("RSSMRA80A01H501U", "CODICE_FISCALE")])

    assert redact(["CF: RSSMRA80A01H501U"], groups, cfg)[0] == ["CF: █████"]


def test_coreference_respects_families() -> None:
    # the model merges a person and an address: the code splits them; out-of-range ids are ignored
    groups, info = _groups(
        pipeline_config(),
        [
            E("Mario Rossi", "PERSONA"),
            E("Via Roma 1", "INDIRIZZO"),
            E("Rossi", "COGNOME"),
        ],
        json.dumps({"groups": [[1, 2, 3, 99]]}),
    )

    by_category = {g.category: sorted(e.text for e in g.values) for g in groups}
    assert by_category == {
        "PERSONA": ["Mario Rossi", "Rossi"],
        "INDIRIZZO": ["Via Roma 1"],
    }
    assert info == {"method": "vlm", "groups": 2}


def test_free_coreference_only_for_names_places_addresses() -> None:
    # different dates and codes stay apart even if the model merges them; spacing variants do not
    entities = [
        E("01/03/1980", "DATA_NASCITA"),
        E("12/09/2026", "DATA_NASCITA"),
        E("RSSMRA80A01H501U", "CODICE_FISCALE"),
        E("RSS MRA 80A01 H501U", "CODICE_FISCALE"),
        E("Mario Rossi", "PERSONA"),
        E("ROSSI M.", "PERSONA"),
    ]

    groups, _ = _groups(
        pipeline_config(), entities, '{"groups": [[1, 2], [3, 4], [5, 6]]}'
    )

    assert sorted(len(g.values) for g in groups) == [1, 1, 2, 2]
    by_text = {e.text: i for i, g in enumerate(groups) for e in g.values}
    assert by_text["01/03/1980"] != by_text["12/09/2026"]
    assert by_text["RSSMRA80A01H501U"] == by_text["RSS MRA 80A01 H501U"]
    assert by_text["Mario Rossi"] == by_text["ROSSI M."]


def test_group_category_is_the_most_informative() -> None:
    groups, _ = _groups(
        pipeline_config(),
        [
            E("Rossi", "COGNOME"),
            E("Mario Rossi", "PERSONA"),
            E("dott. Rossi", "MEDICO"),
        ],
        '{"groups": [[1, 2, 3]]}',
    )

    assert [g.category for g in groups] == ["MEDICO"]


def test_exact_coreference_and_fallback() -> None:
    entities = [
        E("Mario Rossi", "PERSONA"),
        E("MARIO  ROSSI", "PERSONA"),
        E("Rossi", "COGNOME"),
    ]

    groups, info = _groups(
        pipeline_config({"phases": {"redaction": {"coreference": "exact"}}}), entities
    )
    assert len(groups) == 2 and info["method"] == "exact"

    groups, info = _groups(pipeline_config(), entities, VLMError("down"))
    assert len(groups) == 2 and info["method"] == "exact (fallback)"


def test_single_value_without_a_call() -> None:
    cfg = pipeline_config()

    def unexpected(*args: object) -> Answer:
        raise AssertionError("unexpected call")

    client = FakeVLM(cfg, unexpected)
    groups, _ = asyncio.run(group_entities([E("Mario Rossi", "PERSONA")], client, cfg))

    assert len(groups) == 1 and client.calls == []


def test_common_words_in_lowercase_are_not_redacted() -> None:
    cfg = pipeline_config()
    groups, _ = _groups(cfg, [E("Rossi", "COGNOME")])

    pages, _ = redact(["Paziente Rossi, globuli rossi"], groups, cfg)

    assert pages == ["Paziente [COGNOME_1], globuli rossi"]


def test_initials_are_not_redacted_inside_dotted_abbreviations() -> None:
    cfg = pipeline_config()
    groups, _ = _groups(cfg, [E("M.", "PERSONA")])

    pages, _ = redact(["Rossi M. - U.M. mg/dL"], groups, cfg)

    assert pages == ["Rossi [PERSONA_1] - U.M. mg/dL"]
