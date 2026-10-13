"""Document specs: validation, PII markers, computed values, locations."""

from typing import Any

import pytest
from pydantic import ValidationError

from anonval.spec import Spec, resolve, sanitize
from anonval.synthetic import codice_fiscale


def _spec(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "referto_laboratorio_facile_01",
        "type": "referto_laboratorio",
        "difficulty": "facile",
        "persons": {
            "paz": {
                "first_name": "Giulia",
                "last_name": "Bianchi",
                "sex": "F",
                "birth_date": "1978-04-12",
                "birth_place": "Bologna",
            }
        },
        "letterhead": {
            "logo": "AUV",
            "lines": ["[[STRUTTURA_SANITARIA:Azienda USL di Valmarina]]"],
        },
        "footer": ["Sede legale: [[INDIRIZZO:via dei Tigli 4]]"],
        "blocks": [
            {
                "type": "patient_box",
                "fields": [
                    ["Paziente", "[[PERSONA:Giulia Bianchi]]"],
                    ["C.F.", "[[CODICE_FISCALE:@paz]]"],
                ],
            },
            {
                "type": "columns",
                "left": [{"type": "paragraph", "text": "IBAN [[IBAN:@c1]]"}],
                "right": [
                    {
                        "type": "char_boxes",
                        "label": "CF",
                        "value": "[[CODICE_FISCALE:@paz]]",
                    }
                ],
            },
            {
                "type": "table",
                "header": ["Esame", "Valore"],
                "rows": [["Glucosio", "[[ESAME_VALORE:98 mg/dL]]"]],
            },
            {
                "type": "signature",
                "name": "Dott. [[MEDICO:Paolo Neri]]",
                "stamp": ["IBAN [[IBAN:@c1]]"],
            },
        ],
        "negatives": [{"value": "Parkinson", "forbidden_categories": ["PERSONA"]}],
    }
    data.update(overrides)
    return data


def test_resolve_replaces_markers_and_records_mentions() -> None:
    spec, mentions = resolve(Spec.model_validate(_spec()))
    box = spec.blocks[0]
    assert box.type == "patient_box"
    cf = codice_fiscale("Giulia", "Bianchi", "F", "1978-04-12", "Bologna")
    assert box.fields == [("Paziente", "Giulia Bianchi"), ("C.F.", cf)]
    found = {(m.value, m.category, m.location) for m in mentions}
    assert ("Azienda USL di Valmarina", "STRUTTURA_SANITARIA", "letterhead") in found
    assert ("via dei Tigli 4", "INDIRIZZO", "footer") in found
    assert (cf, "CODICE_FISCALE", "patient_box") in found
    assert (cf, "CODICE_FISCALE", "char_boxes") in found
    assert ("98 mg/dL", "ESAME_VALORE", "table") in found
    assert ("Paolo Neri", "MEDICO", "signature") in found


def test_same_label_gives_same_computed_value() -> None:
    _, mentions = resolve(Spec.model_validate(_spec()))
    ibans = {m.value for m in mentions if m.category == "IBAN"}
    assert len(ibans) == 1
    assert {m.location for m in mentions if m.category == "IBAN"} == {
        "paragraph",
        "stamp",
    }


def test_unknown_category_is_rejected() -> None:
    data = _spec(footer=["[[NOME_GATTO:Fuffi]]"])
    with pytest.raises(ValueError, match="NOME_GATTO"):
        resolve(Spec.model_validate(data))


def test_unknown_person_is_rejected() -> None:
    data = _spec(footer=["[[CODICE_FISCALE:@tutore]]"])
    with pytest.raises(ValueError, match="tutore"):
        resolve(Spec.model_validate(data))


def test_computed_value_only_for_supported_categories() -> None:
    data = _spec(footer=["[[PERSONA:@paz]]"])
    with pytest.raises(ValueError, match="PERSONA"):
        resolve(Spec.model_validate(data))


def test_malformed_marker_is_rejected() -> None:
    data = _spec(footer=["[[PERSONA Giulia]]"])
    with pytest.raises(ValueError, match="marker"):
        resolve(Spec.model_validate(data))


def test_id_must_match_type_and_difficulty() -> None:
    with pytest.raises(ValidationError):
        Spec.model_validate(_spec(id="referto_laboratorio_media_01"))


def test_unknown_block_field_is_rejected() -> None:
    data = _spec(blocks=[{"type": "paragraph", "txt": "ciao"}])
    with pytest.raises(ValidationError):
        Spec.model_validate(data)


def test_table_rows_must_match_header() -> None:
    data = _spec(blocks=[{"type": "table", "header": ["a", "b"], "rows": [["1"]]}])
    with pytest.raises(ValidationError):
        Spec.model_validate(data)


def test_negative_with_unknown_category_is_rejected() -> None:
    data = _spec(negatives=[{"value": "Down", "forbidden_categories": ["GATTO"]}])
    with pytest.raises(ValueError, match="GATTO"):
        resolve(Spec.model_validate(data))


def test_sanitize_maps_symbols_and_rejects_others() -> None:
    assert sanitize("PSA ≤ 4 ng/mL") == "PSA <= 4 ng/mL"
    with pytest.raises(ValueError):
        sanitize("emoji \U0001f600")
