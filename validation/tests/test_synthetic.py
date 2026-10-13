"""Synthetic identifiers: checksums of codice fiscale, IBAN, partita IVA, health card."""

import pytest

from anonval.synthetic import (
    BIRTH_PLACES,
    codice_fiscale,
    control_char,
    iban,
    luhn_valid,
    partita_iva,
    tessera_sanitaria,
)


def test_control_char_textbook_example() -> None:
    assert control_char("RSSMRA85T10A562") == "S"


def test_codice_fiscale_mario_rossi() -> None:
    cf = codice_fiscale("Mario", "Rossi", "M", "1985-08-01", "Roma")
    assert cf == "RSSMRA85M01H501" + control_char("RSSMRA85M01H501")


def test_codice_fiscale_female_day_and_short_names() -> None:
    cf = codice_fiscale("Ida", "Fo", "F", "1990-01-05", "Milano")
    assert cf[:6] == "FOXDIA"
    assert cf[9:11] == "45"
    assert cf[11:15] == "F205"


def test_codice_fiscale_name_with_four_consonants_and_accents() -> None:
    cf = codice_fiscale("Gianfranco", "D'Agostino-Nicolò", "M", "1972-12-31", "Napoli")
    assert cf[:3] == "DGS"
    assert cf[3:6] == "GFR"


def test_codice_fiscale_unknown_place() -> None:
    with pytest.raises(ValueError):
        codice_fiscale("Mario", "Rossi", "M", "1985-08-01", "Atlantide")


def test_iban_known_example() -> None:
    assert iban("05428", "11101", "000000123456") == "IT60X0542811101000000123456"


def test_iban_random_is_valid_and_deterministic() -> None:
    first = iban(seed="doc:iban1")
    assert first == iban(seed="doc:iban1")
    digits = "".join(str(int(c, 36)) for c in first[4:] + first[:4])
    assert int(digits) % 97 == 1


def test_partita_iva_known_example() -> None:
    assert partita_iva("0636339100") == "06363391001"


def test_partita_iva_random_has_eleven_digits() -> None:
    value = partita_iva(seed="doc:piva")
    assert len(value) == 11 and value.isdigit()
    assert value == partita_iva(value[:10])


def test_tessera_sanitaria_luhn() -> None:
    value = tessera_sanitaria(seed="doc:ts")
    assert len(value) == 20 and value.startswith("80380")
    assert luhn_valid(value)


def test_birth_places_have_belfiore_codes() -> None:
    assert BIRTH_PLACES["Roma"] == "H501"
    assert all(len(code) == 4 for code in BIRTH_PLACES.values())
