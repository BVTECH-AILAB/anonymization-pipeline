import copy
from typing import Any

import pytest

from anonymization_pipeline.detectors import rules as rules_module
from anonymization_pipeline.detectors.rules import (
    FILE,
    RulesDetector,
    fix_cf_ocr,
    rules,
)
from anonymization_pipeline.detectors.rules.validators import (
    luhn,
    valid_card,
    valid_cf,
    valid_iban,
    valid_vat,
    valid_vin,
)


def _found(text: str) -> set[tuple[str, str]]:
    return {(p.category, p.value) for p in RulesDetector().detect(text)}


def test_checksum_validators() -> None:
    assert valid_cf("RSSMRA80A01H501U") and not valid_cf("RSSMRA80A01H501X")
    assert valid_cf("RSSMRA80A01H50MM")  # omocodia: letter in place of a digit
    assert fix_cf_ocr("RSSMRA8OA01H501U") == "RSSMRA80A01H501U"  # O read instead of 0
    assert valid_iban("IT60X0542811101000000123456") and not valid_iban(
        "IT60X0542811101000000123457"
    )
    assert (
        valid_vat("01234567897")
        and not valid_vat("01234567898")
        and not valid_vat("00000000000")
    )
    assert valid_card("4111 1111 1111 1111") and not valid_card("4111 1111 1111 1112")
    assert luhn("79927398713")
    assert valid_vin("1M8GDM9AXKP042788") and not valid_vin("1M8GDM9A1KP042788")


def test_validated_identifiers() -> None:
    text = (
        "CF: RSSMRA80A01H501U - IBAN IT60X0542811101000000123456 - P.IVA 01234567897\n"
        "carta 4111 1111 1111 1111, mail mario.rossi@example.com, PEC studio@pec.it, targa AB 123 CD\n"
        "cell. 333 1234567, IP 192.168.1.20"
    )

    assert _found(text) >= {
        ("CODICE_FISCALE", "RSSMRA80A01H501U"),
        ("IBAN", "IT60X0542811101000000123456"),
        ("PARTITA_IVA", "01234567897"),
        ("CARTA_CREDITO", "4111 1111 1111 1111"),
        ("EMAIL", "mario.rossi@example.com"),
        ("PEC", "studio@pec.it"),
        ("TARGA", "AB 123 CD"),
        ("TELEFONO", "333 1234567"),
        ("INDIRIZZO_IP", "192.168.1.20"),
    }


def test_no_false_positives_without_checksum_or_context() -> None:
    text = "Codice RSSMRA80A01H501X, conto 12345678903, lotto 4111 1111 1111 1112, IP 999.1.1.1, esame 12345"

    # CF and card without checksum, VAT and CAP without context, invalid IP
    assert _found(text) == set()


def test_dates_typed_by_context() -> None:
    text = "Nato il 14/03/1968. Data ricovero 22/09/2026. Visita del 3 marzo 2025.\nFirmato 29/09/2026"

    assert _found(text) == {
        ("DATA_NASCITA", "14/03/1968"),
        ("DATA_RICOVERO", "22/09/2026"),
        ("DATA_VISITA", "3 marzo 2025"),
        ("DATA", "29/09/2026"),
    }


def test_ocr_date_only_near_birth() -> None:
    assert ("DATA_NASCITA", "05/0V/1974") in _found("Nata il 05/0V/1974 a Roma")
    assert not any(v == "05/0V/1974" for _, v in _found("Lotto 05/0V/1974"))


def test_split_address() -> None:
    assert _found("Via degli Ospedali 12, 24100 Valserio (BG) · Tel. 035 000 0000") >= {
        ("INDIRIZZO", "Via degli Ospedali 12"),
        ("CAP", "24100"),
        ("LUOGO", "Valserio (BG)"),
    }


def test_address_parts_never_cross_a_line_break() -> None:
    found = _found(
        "Via Roma 1, 00185 Roma\n\nTel. 06 1234567\n\nVia\n\nTaddeo da Sessa 5"
    )

    assert ("LUOGO", "Roma") in found
    assert not any("\n" in value for _, value in found)
    # two words of a city on the same line are still one value
    assert ("LUOGO", "San Donato (MI)") in _found("Via Emilia 3, 20097 San Donato (MI)")


def test_postal_code_and_city_after_the_street_on_the_next_line() -> None:
    found = _found("Via degli Ospedali 12\n\n50131 Firenze\n\nTel. 055 1234567")

    assert {
        ("INDIRIZZO", "Via degli Ospedali 12"),
        ("CAP", "50131"),
        ("LUOGO", "Firenze"),
    } <= found


def test_icd10_codes() -> None:
    # with the dot anywhere; without it ("J13") only when "ICD" is in the page; never the start of "E11.9"
    table = "| Tipo | Descrizione | ICD-10 |\n| Principale | Polmonite | J13 |\n| Secondaria | Diabete | E11.9 |"
    assert {v for c, v in _found(table) if c == "DIAGNOSI"} == {"J13", "E11.9"}
    assert {
        v
        for c, v in _found("Diagnosi E11.9, vitamina B12 nella norma")
        if c == "DIAGNOSI"
    } == {"E11.9"}


def _broken(change: Any) -> dict:
    data = copy.deepcopy(rules_module.read_yaml(FILE))
    change(data)
    return data


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(pec="@pec\\."), r"unknown keys: pec"),
        (lambda d: d.pop("ocr"), r"\(file\): missing keys: ocr"),
        (lambda d: d["context"].pop("window"), r"\(context\): missing keys: window"),
        (lambda d: d["context"].update(window=0), r"context.window"),
        (lambda d: d["refinements"].update(extra=1), r"unknown keys: extra"),
        (
            lambda d: d["refinements"]["dates"].append({"regex": "x"}),
            r"refinements.dates 8",
        ),
        (
            lambda d: d["patterns"][0].update(category="INESISTENTE"),
            r"unknown category",
        ),
        (lambda d: d.update(patterns=[]), r"\(patterns\): expected a non-empty list"),
    ],
)
def test_rules_file_is_strict(
    monkeypatch: pytest.MonkeyPatch, change: Any, message: str
) -> None:
    data = _broken(change)
    monkeypatch.setattr(rules_module, "read_yaml", lambda name: data)
    rules.cache_clear()
    try:
        with pytest.raises(ValueError, match=message):
            rules()
    finally:
        rules.cache_clear()
